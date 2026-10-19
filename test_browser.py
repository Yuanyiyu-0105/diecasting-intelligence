from playwright.sync_api import sync_playwright


def test_browser():

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page()

        page.goto(
            "https://example.com",
            wait_until="networkidle"
        )

        print(
            "网页标题:",
            page.title()
        )

        print(
            "网页内容:",
            page.locator("body").inner_text()[:200]
        )

        browser.close()


if __name__ == "__main__":
    test_browser()