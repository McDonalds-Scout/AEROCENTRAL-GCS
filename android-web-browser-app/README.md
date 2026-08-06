# Android Web Browser Wrapper

This directory contains an experimental Android-focused Web wrapper. It is independent from the main AeroCentral Ground Control Station and does not modify the parent project files.

## Features

- Mobile-first browser interface for Android devices.
- Address bar for direct URL entry and search.
- Multi-tab browsing.
- Home shortcuts.
- History and bookmarks.
- PWA manifest and service worker support for add-to-home-screen testing.
- Embedded page preview where allowed by the target website.
- External-open option for websites that block iframe embedding.

## Running Locally

Opening `index.html` directly is enough to inspect the interface.

To test PWA installation and service worker behavior, start the local static server in this directory:

```powershell
node server.js
```

Then open:

```text
http://127.0.0.1:8080/
```

## Notes

Web applications cannot bypass website security policies in the same way as a native Android WebView. Many websites block iframe embedding through `X-Frame-Options` or `Content-Security-Policy`. For these websites, the wrapper provides an external-open action so the page can be opened in the system browser.

This wrapper is a separate experiment and is not required for AeroCentral desktop or Web operation.
