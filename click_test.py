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
            # More specific locator for the button
            test_button = page.locator("button:has-text('Test LLM')")
            await test_button.wait_for(timeout=10000)
            
            print("Clicking 'Test LLM' button...")
            await test_button.click()
            
            print("Waiting for result (10s)...")
            # Wait for any status change in the test card
            await asyncio.sleep(10) 
            
            # Take a screenshot to see what's happening (optional, but good for debug)
            await page.screenshot(path="test_result.png")
            
            # Check for success specifically in the button or nearby
            content = await page.content()
            
            if "Success" in content:
                print("тЬК E2E TEST PASSED: 'Success' found on page!")
            elif "Error" in content:
                print("тЬШ E2E TEST FAILED: 'Error' still visible.")
                # Try to find the specific error message
                error_box = page.locator("div:has-text('Error')").last
                if await error_box.is_visible():
                    print(f"Error details: {await error_box.inner_text()}")
            else:
                print("вЪа E2E TEST AMBIGUOUS: No clear Success/Error state.")
                    
        except Exception as e:
            print(f"тЭМ Error during test: {e}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_test())
