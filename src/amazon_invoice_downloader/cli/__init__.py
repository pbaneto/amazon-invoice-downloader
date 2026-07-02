# SPDX-FileCopyrightText: 2023-present David C Wang <dcwangmit01@gmail.com>
#
# SPDX-License-Identifier: MIT

"""
Amazon Invoice Downloader

Usage:
  amazon-invoice-downloader.py \
    [--email=<email> --password=<password>] \
    [--year=<YYYY> | --date-range=<YYYYMMDD-YYYYMMDD>]
  amazon-invoice-downloader.py (-h | --help)
  amazon-invoice-downloader.py (-v | --version)

Login Options:
  --email=<email>          Amazon login email  [default: $AMAZON_EMAIL].
  --password=<password>    Amazon login password  [default: $AMAZON_PASSWORD].

Date Range Options:
  --date-range=<YYYYMMDD-YYYYMMDD>  Start and end date range
  --year=<YYYY>                     Year, formatted as YYYY  [default: <CUR_YEAR>].

Options:
  -h --help                Show this screen.
  -v --version             Show version.

Examples:
  amazon-invoice-downloader.py --year=2022  # Uses .env file or env vars $AMAZON_EMAIL and $AMAZON_PASSWORD
  amazon-invoice-downloader.py --date-range=20220101-20221231
  amazon-invoice-downloader.py --email=user@example.com --password=secret  # Defaults to current year
  amazon-invoice-downloader.py --email=user@example.com --password=secret --year=2022
  amazon-invoice-downloader.py --email=user@example.com --password=secret --date-range=20220101-20221231

Features:
  - Remote debugging enabled on port 9222 for AI MCP Servers
  - Virtual authenticator configured to prevent passkey dialogs
  - Stealth mode enabled to avoid detection

Credential Precedence:
  1. Command line arguments (--email, --password)
  2. Environment variables ($AMAZON_EMAIL, $AMAZON_PASSWORD)
  3. .env file (automatically loaded if env vars not set)
"""

import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from docopt import docopt
from dotenv import load_dotenv
from playwright.sync_api import Error, sync_playwright
from playwright_stealth import Stealth

from ..__about__ import __version__

BASE_URL = "https://www.amazon.es"
SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def parse_spanish_date(card_text: str) -> datetime:
    # First date in the card is the order date, e.g. "24 de junio de 2026".
    match = re.search(r"(\d{1,2}) de (\w+) de (\d{4})", card_text)
    day, month_name, year = int(match.group(1)), match.group(2).lower(), int(match.group(3))
    return datetime(year, SPANISH_MONTHS[month_name], day)


def parse_euro_total(card_text: str) -> str:
    # e.g. "TOTAL\n1.234,56 €" -> "1234.56" (European thousands '.', decimal ',')
    match = re.search(r"TOTAL\s*([\d.,]+)", card_text)
    amount = match.group(1) if match else "0"
    return amount.replace(".", "").replace(",", ".")


def load_env_if_needed():
    """Load environment variables from .env file if it exists and variables aren't set."""
    # Check if Amazon credentials are already set in environment
    amazon_email = os.environ.get('AMAZON_EMAIL')
    amazon_password = os.environ.get('AMAZON_PASSWORD')

    # If both are already set, no need to load .env
    if amazon_email and amazon_password:
        return

    # Look for .env file in current directory and parent directories
    current_dir = Path.cwd()
    env_file = None

    # Check current directory and up to 3 parent directories
    for i in range(4):
        check_path = current_dir / '.env'
        if check_path.exists():
            env_file = check_path
            break
        current_dir = current_dir.parent

    if env_file:
        print(f"Loading environment variables from {env_file}")
        load_dotenv(env_file)
    else:
        print("No .env file found in current directory or parent directories")


def sleep():
    # Add human latency
    # Generate a random sleep time between 3 and 5 seconds
    sleep_time = random.uniform(2, 5)
    # Sleep for the generated time
    time.sleep(sleep_time)


def run(playwright, args):
    email = args.get("--email")
    if email == "$AMAZON_EMAIL":
        email = os.environ.get("AMAZON_EMAIL")

    password = args.get("--password")
    if password == "$AMAZON_PASSWORD":
        password = os.environ.get("AMAZON_PASSWORD")

    # Parse date ranges int start_date and end_date
    if args["--date-range"]:
        start_date, end_date = args["--date-range"].split("-")
    elif args["--year"] != "<CUR_YEAR>":
        start_date, end_date = args["--year"] + "0101", args["--year"] + "1231"
    else:
        year = str(datetime.now().year)
        start_date, end_date = year + "0101", year + "1231"
    start_date = datetime.strptime(start_date, "%Y%m%d")
    end_date = datetime.strptime(end_date, "%Y%m%d")

    # Ensure the location exists for where we will save our downloads
    target_dir = os.getcwd() + "/" + "downloads"
    os.makedirs(target_dir, exist_ok=True)

    # Create Playwright context with Chromium
    # Always use CDP for virtual authenticator and remote debugging
    print("🚀 Launching Chromium with CDP debugging on port 9222")
    print("📱 You can connect to this browser at: http://localhost:9222")
    print("🔗 AI assistant can control this browser instance via CDP")

    # Launch browser with CDP endpoint
    browser = playwright.chromium.launch(
        headless=False,
        args=[
            '--remote-debugging-port=9222',
            '--remote-debugging-address=0.0.0.0',
            '--disable-web-security',
            '--disable-features=VizDisplayCompositor',
        ],
    )

    # Connect to the browser using CDP
    browser = playwright.chromium.connect_over_cdp("http://localhost:9222")

    # Create context and page
    context = browser.new_context()
    # Bound every operation so a stalled page (e.g. an ad iframe that never goes
    # idle) surfaces as a timeout instead of hanging the whole run indefinitely.
    context.set_default_timeout(60000)
    context.set_default_navigation_timeout(60000)
    page = context.new_page()

    # Set up virtual authenticator to prevent passkey dialogs
    print("🔐 Setting up virtual authenticator to disable passkeys")
    try:
        client = page.context.new_cdp_session(page)
        client.send("WebAuthn.enable")
        client.send(
            "WebAuthn.addVirtualAuthenticator",
            {
                "options": {
                    "protocol": "ctap2",
                    "transport": "internal",
                    "hasResidentKey": True,
                    "hasUserVerification": True,
                    "isUserVerified": True,
                    "automaticPresenceSimulation": True,
                }
            },
        )
        print("✅ Virtual authenticator configured successfully")
    except Exception as e:
        print(f"⚠️ Warning: Could not configure virtual authenticator: {e}")

    Stealth().apply_stealth_sync(page)

    # Amazon intermittently serves an anti-bot "Seguir comprando" interstitial or a
    # stripped-down page instead of the full homepage. Retry loading until the
    # account/sign-in link is present, dismissing the interstitial each time.
    sign_in_link = None
    for attempt in range(5):
        page.goto(BASE_URL + "/", wait_until="domcontentloaded")
        page.wait_for_load_state("domcontentloaded")

        continue_button = page.query_selector(
            'button:has-text("Seguir comprando"), input[value="Seguir comprando"], '
            'button:has-text("Continue shopping"), input[value="Continue shopping"]'
        )
        if continue_button:
            print("Dismissing anti-bot interstitial...")
            continue_button.click()
            page.wait_for_load_state("domcontentloaded")
            sleep()

        sign_in_link = page.query_selector('#nav-link-accountList')
        if sign_in_link:
            break
        print(f"Homepage sign-in link not found, retrying ({attempt + 1}/5)...")
        sleep()

    if not sign_in_link:
        raise RuntimeError("Could not reach the Amazon homepage sign-in link after several attempts")

    sign_in_link.click()
    page.wait_for_load_state("domcontentloaded")
    sleep()

    # The login form field ids are stable across locales.
    if email:
        page.fill('#ap_email_login' if page.query_selector('#ap_email_login') else '#ap_email', email)
        continue_button = page.query_selector('#continue')
        if continue_button:
            continue_button.click()
            page.wait_for_load_state("domcontentloaded")
            sleep()

    if password:
        page.fill('#ap_password', password)
        page.click('#signInSubmit')
        page.wait_for_load_state("domcontentloaded")
        sleep()

    # After the password, Amazon may redirect to a Two-Step Verification (OTP) page.
    # The redirect can still be in flight and destroy the DOM context mid-query, so
    # settle the page and tolerate that transient error before/while checking.
    def on_2fa_page():
        for _ in range(3):
            try:
                page.wait_for_load_state("domcontentloaded")
                title = page.title()
                return (
                    "Two-Step Verification" in title
                    or "Verificación en dos pasos" in title
                    or bool(page.query_selector('input#auth-mfa-otpcode'))
                )
            except Error:
                time.sleep(1)
        return False

    if on_2fa_page():
        print("🔐 2FA detected - please complete authentication in browser")
        while on_2fa_page():
            time.sleep(1)
        print("✅ 2FA completed")
    page.wait_for_load_state("domcontentloaded")

    # Navigate straight to the order history (locale-independent URL)
    page.goto(BASE_URL + "/gp/css/order-history?ref_=nav_orders_first", wait_until="domcontentloaded")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector("select#time-filter", timeout=60000)
    sleep()

    # Get a list of years from the select options
    select = page.query_selector("select#time-filter")
    years = select.inner_text().split("\n")  # skip the first two text options

    # Filter years to include only numerical years (YYYY)
    years = [year for year in years if year.isnumeric()]

    # Filter years to the include only the years between start_date and end_date inclusively
    years = [year for year in years if start_date.year <= int(year) <= end_date.year]
    years.sort(reverse=True)
    print(f"Order-history years available in range: {years}")

    # Year Loop (Run backwards through the time range from years to pages to orders)
    for year in years:
        # Select the year in the order filter; retry once if the reload stalls.
        for attempt in range(2):
            try:
                page.select_option("select#time-filter", value=f"year-{year}")
                page.wait_for_selector(".order-card.js-order-card", timeout=30000)
                break
            except Error:
                print(f"Year {year} filter stalled, reloading order history...")
                page.goto(BASE_URL + "/gp/css/order-history?ref_=nav_orders_first", wait_until="domcontentloaded")
                page.wait_for_load_state("domcontentloaded")
        sleep()

        # Page Loop
        first_page = True
        done = False
        while not done:
            # Follow the pagination "next" control (locale-independent .a-last class);
            # if it is missing or disabled there are no more pages.
            if not first_page:
                next_link = page.query_selector("ul.a-pagination li.a-last:not(.a-disabled) a")
                if not next_link:
                    break
                next_link.click()
                page.wait_for_load_state("domcontentloaded")
            first_page = False
            sleep()

            # Order Loop
            order_cards = page.query_selector_all(".order-card.js-order-card")
            for order_card in order_cards:
                card_text = order_card.inner_text()

                # Skip cancelled orders
                if "cancelado" in card_text.lower():
                    continue

                date = parse_spanish_date(card_text)
                total = parse_euro_total(card_text)
                details = order_card.query_selector('a[href*="orderID="]')
                orderid = re.search(r"orderID=([0-9-]+)", details.get_attribute("href")).group(1)
                date_str = date.strftime("%Y%m%d")
                base_name = f"{target_dir}/{date_str}_{total}_amazon_{orderid}"

                if date > end_date:
                    continue
                elif date < start_date:
                    done = True
                    break

                # Open the order's "Factura" popover and download the real invoice
                # PDF(s). Orders where the seller has not issued one only offer
                # "Solicitar factura" and are skipped.
                trigger = order_card.query_selector('a[href*="invoice/popover"]')
                if not trigger:
                    print(f"⚠️ No invoice option for {orderid}; skipping")
                    continue
                popover_html = context.request.get(BASE_URL + trigger.get_attribute("href")).text()
                anchors = re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', popover_html, re.S)
                factura_hrefs = [
                    href for href, text in anchors if re.sub(r"<[^>]*>", "", text).strip().startswith("Factura")
                ]
                if not factura_hrefs:
                    print(f"⚠️ No factura available for {orderid} (only 'Solicitar factura'); skipping")
                    continue

                for index, href in enumerate(factura_hrefs):
                    file_name = f"{base_name}.pdf" if len(factura_hrefs) == 1 else f"{base_name}_{index + 1}.pdf"
                    if os.path.isfile(file_name):
                        print(f"File [{file_name}] already exists")
                        continue
                    print(f"Saving invoice [{file_name}]")
                    try:
                        response = context.request.get(BASE_URL + href)
                        with open(file_name, "wb") as invoice_file:
                            invoice_file.write(response.body())
                    except Error as exc:
                        print(f"⚠️ Skipped invoice for {orderid}: {exc}")

    # Close the browser
    context.close()
    browser.close()


def amazon_invoice_downloader():
    # Load environment variables from .env file if needed
    load_env_if_needed()

    args = docopt(__doc__)
    # print(args)
    if args['--version']:
        print(__version__)
        sys.exit(0)

    with sync_playwright() as playwright:
        run(playwright, args)
