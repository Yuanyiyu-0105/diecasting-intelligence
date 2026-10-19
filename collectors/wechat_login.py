"""微信公众号采集 · 登录态建立（任务书附录C：wechat_login.py）。

用途：
    搜狗微信（weixin.sogou.com）有反爬验证码，wechat_crawler.py 需要一份
    已通过的浏览器会话（cookie）。本脚本用 Playwright 持久化 profile
    （data/wechat_profile/）保存这份会话。

用法：
    .venv\\Scripts\\python.exe collectors\\wechat_login.py

流程：
    1. 弹出一个 Chromium 窗口（带界面），自动打开搜狗微信搜索页；
    2. 你在窗口里完成验证码（滑块/点选）；
    3. 一旦检测到搜索结果出现，脚本自动结束保存（也可直接关闭窗口）；
    4. 会话自动写入 data/wechat_profile/，之后 crawler 可复用。

会话有效期：搜狗 cookie（SNUID/SUV）通常能撑数天到数周。
crawler 日志一旦提示「检测到验证码」，就重跑本脚本过一次即可。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROFILE_DIR = os.path.join("data", "wechat_profile")
# 注意：搜狗「搜公众号」接口（type=1）已下线，这里用「搜文章」（type=2）做验证
TEST_URL = "https://weixin.sogou.com/weixin?type=2&query=%E5%8E%8B%E9%93%B8&ie=utf8"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def main():
    from playwright.sync_api import sync_playwright

    os.makedirs(PROFILE_DIR, exist_ok=True)
    print("=" * 56)
    print(" 微信登录态建立（搜狗微信）")
    print(" 即将弹出浏览器窗口，请在窗口中：")
    print("   1. 等待搜狗微信搜索页加载；")
    print("   2. 如出现验证码，人工完成验证；")
    print("   3. 能看到正常搜索结果列表 = 会话就绪，脚本会自动结束保存；")
    print(" 也随时可以直接关闭浏览器窗口结束。")
    print("=" * 56)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            viewport={"width": 1280, "height": 860},
            user_agent=UA,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(TEST_URL, timeout=30000, wait_until="domcontentloaded")
        print("\n>>> 等待验证完成（最多 10 分钟）...")

        # 自动检测：搜到结果 or 用户关掉窗口 即视为完成
        for _ in range(300):
            time.sleep(2)
            try:
                if page.is_closed():
                    print("窗口已关闭")
                    break
                if "antispider" in page.url:
                    continue
                if page.locator(".news-list li").count() > 0:
                    print("检测到搜索结果，会话有效")
                    break
            except Exception:
                break
        ctx.close()

    print(f"登录态已保存到 {PROFILE_DIR}/")
    print("现在可以运行: .venv\\Scripts\\python.exe collectors\\wechat_crawler.py")


if __name__ == "__main__":
    main()
