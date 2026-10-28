const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ZXing = require("../attendance_app/static/vendor/zxing.min.js");
const source = fs.readFileSync(path.join(__dirname, "../attendance_app/static/js/camera-scan.js"), "utf8");

function load(overrides = {}, mediaDevices = { getUserMedia() {} }) {
  const context = {
    drawImage(video) { this.video = video; },
    getImageData() { return { data: this.video.pixels }; },
  };
  const canvas = { width: 0, height: 0, getContext: () => context };
  const window = { ZXing, ...overrides };
  vm.runInNewContext(source, {
    window,
    navigator: { mediaDevices },
    document: { createElement: () => canvas },
  });
  return { scanner: window.CameraScan, canvas };
}

function frame(blank = false) {
  const size = 240;
  const matrix = new ZXing.QRCodeWriter().encode("QA-2026-0001", ZXing.BarcodeFormat.QR_CODE, size, size, new Map());
  const pixels = new Uint8ClampedArray(size * size * 4).fill(255);
  if (!blank) {
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (matrix.get(x, y)) pixels.fill(0, (y * size + x) * 4, (y * size + x) * 4 + 3);
      }
    }
  }
  return { videoWidth: size, videoHeight: size, pixels };
}

test("bundled decoder reads QR without native BarcodeDetector", async () => {
  const { scanner } = load();
  assert.equal(scanner.available(), true);
  const detector = await scanner.create();
  assert.equal(detector.engine, "zxing");
  assert.equal((await detector.detect(frame()))[0].rawValue, "QA-2026-0001");
  assert.equal((await detector.detect(frame(true))).length, 0);
  assert.equal((await detector.detect({ videoWidth: 0, videoHeight: 0 })).length, 0);
  detector.dispose();
});

test("fallback downsizes frames and preserves their aspect ratio", async () => {
  const { scanner, canvas } = load();
  const detector = await scanner.create();
  await detector.detect({ videoWidth: 1280, videoHeight: 720, pixels: new Uint8ClampedArray(800 * 450 * 4).fill(255) });
  assert.equal(canvas.width, 800);
  assert.equal(canvas.height, 450);
  detector.dispose();
});

test("available native decoder is preferred", async () => {
  class Native {
    static async getSupportedFormats() { return ["code_128", "unsupported"]; }
    constructor({ formats }) { assert.equal(formats.join(), "code_128"); }
    async detect() { return [{ rawValue: "123456" }]; }
  }
  const detector = await load({ BarcodeDetector: Native }).scanner.create();
  assert.equal(detector.engine, "native");
  assert.equal((await detector.detect({}))[0].rawValue, "123456");
  detector.dispose();
});

for (const failure of ["empty", "probe", "constructor"]) {
  test(`native ${failure} failure uses bundled decoder`, async () => {
    class Native {
      static async getSupportedFormats() {
        if (failure === "probe") throw new Error("unsupported");
        return failure === "empty" ? [] : ["code_128"];
      }
      constructor() { throw new Error("unsupported"); }
    }
    const detector = await load({ BarcodeDetector: Native }).scanner.create();
    assert.equal(detector.engine, "zxing");
    detector.dispose();
  });
}

test("missing camera API or decoder is reported", async () => {
  assert.equal(load({}, null).scanner.available(), false);
  const { scanner } = load({ ZXing: undefined });
  assert.equal(scanner.available(), false);
  await assert.rejects(scanner.create(), /no_decoder/);
});

test("unexpected frame errors are not hidden", async () => {
  const { scanner } = load();
  const detector = await scanner.create();
  await assert.rejects(detector.detect({ videoWidth: 10, videoHeight: 10, get pixels() { throw new Error("capture failed"); } }), /capture failed/);
  detector.dispose();
});
