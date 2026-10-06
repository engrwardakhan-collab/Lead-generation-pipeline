import asyncio
from rebrowser_playwright.async_api import async_playwright


async def debug():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = await context.new_page()
        # Visit homepage first to establish a real session
        await page.goto("https://www.realtor.com/", wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(5)

        # Then navigate to agent listing
        await page.goto(
            "https://www.realtor.com/realestateagents/Miami_FL/",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await asyncio.sleep(5)

        # Check page title and URL (detect redirects/blocks)
        print("Title:", await page.title())
        print("URL:", page.url)

        # Count all agent links
        links = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href*=\"/realestateagents/\"]')).map(a => a.href)"
        )
        print(f"\nTotal agent links found: {len(links)}")
        for link in links[:10]:
            print(" ", link)

        # Check for common card selectors
        for sel in [
            "[data-testid='agent-list-card']",
            "[class*='agent-list-card']",
            "[class*='AgentCard']",
            "[class*='agent-card']",
            "[class*='AgentListItem']",
            "[class*='agentCard']",
        ]:
            count = await page.locator(sel).count()
            if count:
                print(f"\nSelector '{sel}' found {count} elements")

        # Sample raw HTML snippet to see current structure
        snippet = await page.evaluate(
            "() => document.body.innerHTML.slice(0, 2000)"
        )
        print("\n--- HTML SNIPPET ---")
        print(snippet[:1000])

        await browser.close()


asyncio.run(debug())
