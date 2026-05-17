import asyncio

from playwright.async_api import async_playwright


async def run_test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print("Opening SkyrimNet test page...")
        await page.goto("http://localhost:8080/test")

        print("Waiting for 'Test LLM' button...")
        try:
            test_button = page.locator("button:has-text('Test LLM')")
            await test_button.wait_for(timeout=10000)

            print("Clicking 'Test LLM' button...")
            await test_button.click()

            print("Waiting for result (10s)...")
            await asyncio.sleep(10)

            await page.screenshot(path="test_result.png")

            # Scope the success / error check to the result region rather than
            # the entire HTML page. The previous `"Success" in content` matched
            # static template strings and gave false positives on every run.
            region_text = ""
            try:
                container = page.locator(
                    "xpath=(//button[normalize-space()='Test LLM']"
                    "/ancestor::*[self::div or self::section])[last()]"
                )
                if await container.count() > 0:
                    region_text = await container.first.inner_text(timeout=2000)
            except Exception as inner:
                print(f"[WARN] Failed to scope result region: {inner}")
            if not region_text:
                region_text = await page.content()

            has_success = "Success" in region_text
            has_error = "Error" in region_text

            if has_success and not has_error:
                print("[OK] E2E TEST PASSED: 'Success' found in result region.")
            elif has_error:
                print("[FAIL] E2E TEST FAILED: 'Error' still visible.")
                try:
                    error_box = page.locator("div:has-text('Error')").last
                    if await error_box.is_visible():
                        print(
                            f"Error details: {await error_box.inner_text(timeout=2000)}"
                        )
                except Exception as ex:
                    print(f"[WARN] Could not read error box: {ex}")
            else:
                print("[WARN] E2E TEST AMBIGUOUS: No clear Success/Error state.")

        except Exception as e:
            print(f"[ERROR] Error during test: {e}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(run_test())
