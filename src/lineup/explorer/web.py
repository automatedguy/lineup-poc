"""Web explorer using Playwright.

Crawls the target application, discovers routes and interactive
elements, and builds an AppMap for the test generator.
"""

from __future__ import annotations

import os
import time
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, Page, async_playwright
from rich.console import Console

from lineup.core.config import ScanConfig
from lineup.core.interfaces import Explorer
from lineup.core.models import (
    AppMap,
    ElementType,
    PageElement,
    PageSnapshot,
    Route,
)

console = Console()

# Map HTML tags/roles to our ElementType
ELEMENT_MAP = {
    "a": ElementType.LINK,
    "button": ElementType.BUTTON,
    "input": ElementType.INPUT,
    "select": ElementType.SELECT,
    "textarea": ElementType.TEXTAREA,
    "form": ElementType.FORM,
    "img": ElementType.IMAGE,
}


class WebExplorer(Explorer):
    """Explores a web application using Playwright."""

    def __init__(self, config: ScanConfig) -> None:
        self.config = config
        self._browser: Browser | None = None
        self._visited: set[str] = set()

    async def _get_browser(self) -> Browser:
        if self._browser is None:
            pw = await async_playwright().start()
            self._browser = await pw.chromium.launch(
                headless=self.config.browser.headless
            )
        return self._browser

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None

    def _is_same_origin(self, base_url: str, url: str) -> bool:
        """Only explore URLs on the same domain."""
        base = urlparse(base_url)
        target = urlparse(url)
        return base.netloc == target.netloc

    def _should_ignore(self, url: str) -> bool:
        """Skip URLs that match ignore patterns."""
        url_lower = url.lower()
        return any(pattern in url_lower for pattern in self.config.explorer.ignore_patterns)

    def _normalize_url(self, url: str) -> str:
        """Remove fragments and trailing slashes for deduplication."""
        parsed = urlparse(url)
        clean = parsed._replace(fragment="")
        return clean.geturl().rstrip("/")

    async def _extract_elements(self, page: Page) -> list[PageElement]:
        """Extract interactive elements from the current page."""
        elements = []
        selectors = "a, button, input, select, textarea, form, [role='button'], [role='link']"

        try:
            locators = page.locator(selectors)
            count = await locators.count()

            for i in range(min(count, 200)):  # Cap at 200 elements per page
                loc = locators.nth(i)
                try:
                    tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                    is_visible = await loc.is_visible()
                    text = (await loc.inner_text()).strip()[:100] if tag not in ("input", "select") else ""
                    href = await loc.get_attribute("href") or ""
                    el_type = await loc.get_attribute("type") or ""
                    name = await loc.get_attribute("name") or ""
                    placeholder = await loc.get_attribute("placeholder") or ""
                    aria_label = await loc.get_attribute("aria-label") or ""
                    role = await loc.get_attribute("role") or ""

                    # Generate a useful selector
                    el_id = await loc.get_attribute("id")
                    if el_id:
                        selector = f"#{el_id}"
                    elif name:
                        selector = f"{tag}[name='{name}']"
                    elif aria_label:
                        selector = f"{tag}[aria-label='{aria_label}']"
                    elif text and tag in ("a", "button"):
                        safe_text = text[:30].replace("'", "\\'")
                        selector = f"{tag}:has-text('{safe_text}')"
                    else:
                        selector = f"{tag}:nth-of-type({i + 1})"

                    element_type = ELEMENT_MAP.get(tag, ElementType.OTHER)
                    if role == "button":
                        element_type = ElementType.BUTTON
                    elif role == "link":
                        element_type = ElementType.LINK

                    attrs = {}
                    if href:
                        attrs["href"] = href
                    if el_type:
                        attrs["type"] = el_type
                    if placeholder:
                        attrs["placeholder"] = placeholder
                    if name:
                        attrs["name"] = name

                    elements.append(PageElement(
                        selector=selector,
                        element_type=element_type,
                        text=text[:100],
                        attributes=attrs,
                        is_visible=is_visible,
                    ))
                except Exception:
                    continue  # Skip elements that can't be inspected

        except Exception as e:
            console.print(f"[yellow]Warning extracting elements: {e}[/]")

        return elements

    async def _get_html_summary(self, page: Page) -> str:
        """Get a cleaned HTML summary for LLM consumption.

        Strips scripts, styles, and unnecessary attributes to keep
        the token count manageable for the LLM.
        """
        return await page.evaluate("""() => {
            const clone = document.body.cloneNode(true);
            // Remove noise
            clone.querySelectorAll('script, style, noscript, svg, path, meta, link')
                .forEach(el => el.remove());
            // Clean attributes except essentials
            clone.querySelectorAll('*').forEach(el => {
                const keep = ['id', 'name', 'type', 'href', 'action', 'method',
                              'placeholder', 'aria-label', 'role', 'value', 'class'];
                [...el.attributes].forEach(attr => {
                    if (!keep.includes(attr.name)) el.removeAttribute(attr.name);
                });
            });
            // Truncate
            const html = clone.innerHTML;
            return html.substring(0, 8000);
        }""")

    async def take_snapshot(self, url: str) -> PageSnapshot:
        """Capture a full snapshot of a page."""
        browser = await self._get_browser()
        page = await browser.new_page(
            viewport={
                "width": self.config.browser.viewport_width,
                "height": self.config.browser.viewport_height,
            }
        )

        try:
            await page.goto(url, wait_until="domcontentloaded",
                          timeout=self.config.browser.timeout)
            await page.wait_for_timeout(self.config.explorer.wait_after_navigation)

            title = await page.title()
            elements = await self._extract_elements(page)
            html_summary = await self._get_html_summary(page)

            # Screenshot
            screenshot_path = None
            screenshots_dir = self.config.browser.screenshots_dir
            if screenshots_dir:
                os.makedirs(screenshots_dir, exist_ok=True)
                safe_name = urlparse(url).path.strip("/").replace("/", "_") or "index"
                screenshot_path = os.path.join(screenshots_dir, f"{safe_name}.png")
                await page.screenshot(path=screenshot_path, full_page=True)

            return PageSnapshot(
                url=url,
                title=title,
                html_summary=html_summary,
                elements=elements,
                screenshot_path=screenshot_path,
            )
        finally:
            await page.close()

    async def explore(self, base_url: str, max_depth: int | None = None) -> AppMap:
        """Explore the application starting from base_url."""
        if max_depth is None:
            max_depth = self.config.explorer.max_depth

        start_time = time.time()
        routes: list[Route] = []
        to_visit: list[tuple[str, int, str | None]] = [(base_url, 0, None)]
        self._visited = set()

        browser = await self._get_browser()

        console.print(f"\n[bold cyan]Exploring[/] {base_url}")
        console.print(f"  Max depth: {max_depth}, Max pages: {self.config.explorer.max_pages}\n")

        while to_visit and len(routes) < self.config.explorer.max_pages:
            url, depth, parent = to_visit.pop(0)
            normalized = self._normalize_url(url)

            if normalized in self._visited:
                continue
            if depth > max_depth:
                continue
            if not self._is_same_origin(base_url, url):
                continue
            if self._should_ignore(url):
                continue

            self._visited.add(normalized)
            console.print(f"  [dim]depth={depth}[/] {url}")

            page = await browser.new_page(
                viewport={
                    "width": self.config.browser.viewport_width,
                    "height": self.config.browser.viewport_height,
                }
            )

            try:
                response = await page.goto(url, wait_until="domcontentloaded",
                                         timeout=self.config.browser.timeout)
                if response and response.status >= 400:
                    console.print(f"    [red]HTTP {response.status}[/]")
                    continue

                await page.wait_for_timeout(self.config.explorer.wait_after_navigation)

                title = await page.title()
                elements = await self._extract_elements(page)

                route = Route(
                    url=url,
                    title=title,
                    depth=depth,
                    discovered_from=parent,
                    elements=elements,
                )
                routes.append(route)

                # Discover links for further exploration
                if depth < max_depth:
                    links = [
                        el for el in elements
                        if el.element_type == ElementType.LINK
                        and "href" in el.attributes
                    ]
                    for link in links:
                        href = link.attributes["href"]
                        full_url = urljoin(url, href)
                        norm = self._normalize_url(full_url)
                        if norm not in self._visited:
                            to_visit.append((full_url, depth + 1, url))

            except Exception as e:
                console.print(f"    [red]Error: {e}[/]")
            finally:
                await page.close()

        total_elements = sum(len(r.elements) for r in routes)
        duration = time.time() - start_time

        console.print(f"\n[bold green]Exploration complete[/]")
        console.print(f"  Routes: {len(routes)}, Elements: {total_elements}, Time: {duration:.1f}s\n")

        return AppMap(
            base_url=base_url,
            routes=routes,
            total_elements=total_elements,
            scan_duration_seconds=duration,
        )
