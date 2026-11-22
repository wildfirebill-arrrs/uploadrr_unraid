This directory is for saving your cookies.

---

## Trackers and Cookie Handling

* **Auto-login / no manual seeding needed:** PTP, TTG, FL, MTV, AR, CRT (All other API Based Trackers- duh!)
* **Manual cookie seeding required:** HDT, AZ (AvistaZ), CZ (CinemaZ), PHD (PrivateHD)

> Trackers that require manual cookies cannot log in programmatically; you must export cookies from your browser.

---

## How to Seed Cookies Manually

1. Install the browser extension **Get cookies.txt LOCALLY**:

   * Chrome / Edge: [Link](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   * Firefox: [Link](https://addons.mozilla.org/en-US/firefox/addon/get-cookies-txt-locally/)
   * Source: [GitHub](https://github.com/kairi003/Get-cookies.txt-LOCALLY)

2. Log in to the tracker in your browser.

3. Open the extension, set **Format** to **Netscape**, and click **Export**.

4. Save the file in `data/cookies/` with the following naming convention:

   * HDT → `HDT.txt`
   * AZ → `AZ.txt`
   * CZ → `CZ.txt`
   * PHD → `PHD.txt`

> On next run, CookieMonster will eat 'em cookies and you'll be uploading like a champ!

---

## Troubleshooting

* **"No Valid Cookies" / upload fails** — Your session likely expired. Re-export fresh cookies.
* **2FA sites** — If the tracker enables 2FA, auto-login may break. Some trackers support `otp_uri` for TOTP (e.g., MTV).