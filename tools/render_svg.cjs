// Render a local SVG file to PNG with Playwright Chromium.
// Usage: node render_svg.cjs <input.svg> <output.png> <width> <height>
// Every network request from the page is aborted: nothing leaves the machine.
'use strict';
const fs = require('fs');
const { chromium } = require('playwright');

async function main() {
  const [svgPath, outPath, w, h] = process.argv.slice(2);
  if (!svgPath || !outPath || !w || !h) {
    console.error('usage: node render_svg.cjs <in.svg> <out.png> <width> <height>');
    process.exit(2);
  }
  const width = parseInt(w, 10);
  const height = parseInt(h, 10);
  const svg = fs.readFileSync(svgPath, 'utf8').replace(/^<\?xml[^>]*>\s*/, '');
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({
      viewport: { width, height },
      deviceScaleFactor: 1,
      javaScriptEnabled: false,
    });
    await context.route('**/*', (route) => route.abort());
    const page = await context.newPage();
    await page.setContent(
      '<!doctype html><html><head><style>html,body{margin:0;padding:0;background:#fff}' +
        'svg{display:block}</style></head><body>' + svg + '</body></html>'
    );
    await page.screenshot({ path: outPath, clip: { x: 0, y: 0, width, height } });
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  console.error(String(e && e.stack ? e.stack : e));
  process.exit(1);
});
