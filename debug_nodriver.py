import asyncio
import nodriver as uc


async def debug():
    browser = await uc.start(
        headless=False,
        browser_executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )

    page = await browser.get("https://www.realtor.com/realestateagents/Miami_FL/")
    await asyncio.sleep(5)

    title = await page.evaluate("document.title")
    print("Title:", title)

    links = await page.evaluate(
        "Array.from(document.querySelectorAll('a[href*=\"/realestateagents/\"]')).map(a => a.href)"
    )
    print(f"Agent links found: {len(links)}")
    for link in links[:5]:
        print(" ", link)

    snippet = await page.evaluate("document.body.innerHTML.slice(0, 500)")
    print("\n--- HTML ---")
    print(snippet)

    await browser.stop()


asyncio.run(debug())
