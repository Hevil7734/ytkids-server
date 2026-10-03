import sys
import time
import re
from playwright.sync_api import sync_playwright, expect

def run_test():
    print("=== Starting YouTube Kids Queue UI Playwright Test ===")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))

        print("1. Navigating to http://127.0.0.1:5000 ...")
        page.goto("http://127.0.0.1:5000", wait_until="networkidle")
        assert "YouTube Kids" in page.title() or page.locator(".ytk-logo").is_visible()
        print("   ✓ Page loaded successfully")

        print("2. Opening Grown-ups parental lock...")
        page.locator(".grownups-btn").click()
        page.wait_for_selector("#parentLockModal.active", timeout=5000)

        math_text = page.locator("#lockMathText").text_content()
        print(f"   Math problem: {math_text}")
        m = re.search(r"(\d+)\s*[×x*]\s*(\d+)", math_text)
        assert m, f"Could not parse math problem: {math_text}"
        ans = int(m.group(1)) * int(m.group(2))
        print(f"   Calculated answer: {ans}")

        page.locator("#lockAnswerInput").fill(str(ans))
        page.locator(".lock-confirm-btn").click()

        print("3. Verifying Grown-ups modal is active...")
        page.wait_for_selector("#parentSettingsModal.active", timeout=5000)
        print("   ✓ Grown-ups parental dashboard open")

        # Stop any previous leftover downloads if active
        try:
            page.evaluate("() => fetch('/api/stop', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({stop_all:true})})")
            time.sleep(1)
        except Exception:
            pass

        print("4. Searching first channel (Paw Patrol)...")
        with page.expect_response(lambda res: "/api/search" in res.url and res.status == 200, timeout=20000):
            page.locator(".preset-chip", has_text="Paw Patrol").click()
        
        card1 = page.locator(".channels-grid .channel-card", has_text="PAW Patrol").first
        card1.wait_for(timeout=10000)
        print("   ✓ Channels found for Paw Patrol")

        print("5. Selecting first channel and starting download...")
        card1.locator(".channel-pick-btn").click()
        page.wait_for_selector("#downloadConfigCard.active", timeout=5000)
        
        page.locator("#dlLimitSelect").select_option("5")
        first_folder = page.locator("#dlFolderInput").input_value()
        print(f"   First channel folder: {first_folder}")

        with page.expect_response(lambda res: "/api/download" in res.url and res.status == 200, timeout=15000) as dl_resp:
            page.locator(".start-dl-btn").click()

        dl_data1 = dl_resp.value.json()
        print(f"   First download API response: {dl_data1}")
        assert dl_data1.get("queued") is False, "First download should start immediately, not queued"

        print("6. Waiting for first download progress card...")
        page.wait_for_selector("#dlProgressCard.active", timeout=8000)
        time.sleep(2)

        # Verify /api/status responds with active download
        status_res = page.evaluate("() => fetch('/api/status').then(r => r.json())")
        print(f"   Status check 1: active={status_res.get('active')}, status={status_res.get('status')}, queue_count={status_res.get('queue_count')}")
        assert status_res.get("active") is True, "First download should be active"

        print("7. Searching second channel (Numberblocks) while first download is running...")
        with page.expect_response(lambda res: "/api/search" in res.url and res.status == 200, timeout=20000):
            page.locator(".preset-chip", has_text="Numberblocks").click()
        
        card2 = page.locator(".channels-grid .channel-card", has_text="Numberblocks").first
        card2.wait_for(timeout=10000)
        print("   ✓ Channels found for Numberblocks")

        print("8. Selecting second channel and queueing it...")
        card2.locator(".channel-pick-btn").click()
        page.wait_for_selector("#downloadConfigCard.active", timeout=5000)
        page.locator("#dlLimitSelect").select_option("5")
        second_folder = page.locator("#dlFolderInput").input_value()
        print(f"   Second channel folder: {second_folder}")

        with page.expect_response(lambda res: "/api/download" in res.url and res.status == 200, timeout=15000) as dl_resp2:
            page.locator(".start-dl-btn").click()

        dl_data2 = dl_resp2.value.json()
        print(f"   Second download API response: {dl_data2}")
        assert dl_data2.get("queued") is True, f"Second download MUST be queued, got: {dl_data2}"
        assert dl_data2.get("queue_position") == 1, f"Queue position should be 1, got {dl_data2.get('queue_position')}"

        print("9. Verifying download queue section and badge appear in UI...")
        # Check banner
        expect(page.locator("#dlNotificationBanner")).to_contain_text("queue")
        banner_text = page.locator("#dlNotificationBanner").text_content()
        print(f"   Banner notification text: '{banner_text}'")

        # Wait for queue section to be visible
        page.wait_for_selector("#dlQueueSection", state="visible", timeout=8000)
        expect(page.locator("#dlQueueBadge")).to_have_text("1")
        print(f"   ✓ Queue badge is 1")

        queue_items = page.locator("#dlQueueList > div")
        expect(queue_items).to_have_count(1)
        first_item_text = queue_items.first.text_content()
        print(f"   Queue item content: {first_item_text}")
        assert "Numberblocks" in first_item_text, f"Expected Numberblocks in queue item, got {first_item_text}"

        print("10. Testing adding a third channel (Blippi) to queue...")
        with page.expect_response(lambda res: "/api/search" in res.url and res.status == 200, timeout=20000):
            page.locator(".preset-chip", has_text="Blippi").click()
        
        card3 = page.locator(".channels-grid .channel-card", has_text="Blippi").first
        card3.wait_for(timeout=10000)
        card3.locator(".channel-pick-btn").click()
        page.wait_for_selector("#downloadConfigCard.active", timeout=5000)

        with page.expect_response(lambda res: "/api/download" in res.url and res.status == 200, timeout=15000) as dl_resp3:
            page.locator(".start-dl-btn").click()

        dl_data3 = dl_resp3.value.json()
        print(f"   Third download API response: {dl_data3}")
        assert dl_data3.get("queued") is True

        expect(page.locator("#dlQueueBadge")).to_have_text("2")
        expect(page.locator("#dlQueueList > div")).to_have_count(2)
        print("   ✓ Queue badge updated to 2 and 2 items listed in DOM")
        
        status_res_2 = page.evaluate("() => fetch('/api/status').then(r => r.json())")
        print(f"   Status check 2: active={status_res_2.get('active')}, queue_count={status_res_2.get('queue_count')}")
        assert status_res_2.get("queue_count") == 2, "Backend status should report 2 queued jobs"

        print("11. Taking screenshot of verified download queue UI...")
        page.screenshot(path="/home/ark/queue_verified.png")
        print("   ✓ Screenshot saved to /home/ark/queue_verified.png")

        print("12. Testing removing an item from the queue via UI...")
        remove_btn = page.locator("#dlQueueList button").first
        with page.expect_response(lambda res: "/api/queue/remove" in res.url and res.status == 200, timeout=10000):
            remove_btn.click()

        expect(page.locator("#dlQueueBadge")).to_have_text("1")
        expect(page.locator("#dlQueueList > div")).to_have_count(1)
        print("   ✓ Queue badge count is 1 after removing an item")

        print("13. Cleaning up test download processes...")
        page.evaluate("() => fetch('/api/stop', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({stop_all:true})})")
        time.sleep(1)

        browser.close()
        print("=== All Playwright Queue UI Tests Passed Successfully! ===")

if __name__ == "__main__":
    try:
        run_test()
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}", file=sys.stderr)
        sys.exit(1)
