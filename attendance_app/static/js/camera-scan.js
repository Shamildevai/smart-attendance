(function () {
  "use strict";

  const NATIVE_FORMATS = ["code_128", "code_39", "code_93", "qr_code", "ean_13", "ean_8", "itf", "codabar"];
  const ZXING_FORMATS = ["CODE_128", "CODE_39", "CODE_93", "QR_CODE", "EAN_13", "EAN_8", "ITF", "CODABAR"];

  function nativeDetectorClass() {
    return typeof window.BarcodeDetector === "function" ? window.BarcodeDetector : null;
  }

  function zxingClass() {
    if (!window.ZXing) return null;
    return typeof window.ZXing.MultiFormatReader === "function" ? window.ZXing : null;
  }

  function available() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return false;
    return Boolean(nativeDetectorClass() || zxingClass());
  }

  async function supportedNativeFormats(Detector) {
    if (typeof Detector.getSupportedFormats !== "function") {
      return ["code_128", "qr_code"];
    }
    let formats = [];
    try {
      formats = await Detector.getSupportedFormats();
    } catch (error) {
      formats = [];
    }
    return NATIVE_FORMATS.filter((format) => formats.includes(format));
  }

  function nativeAdapter(detector) {
    return {
      engine: "native",
      async detect(video) {
        const codes = await detector.detect(video);
        return (codes || []).map((code) => ({ rawValue: String(code.rawValue || "") }));
      },
      dispose() {},
    };
  }

  function zxingAdapter() {
    const ZXing = zxingClass();
    const hints = new Map();
    hints.set(
      ZXing.DecodeHintType.POSSIBLE_FORMATS,
      ZXING_FORMATS.map((name) => ZXing.BarcodeFormat[name])
    );
    const reader = new ZXing.MultiFormatReader();
    reader.setHints(hints);
    const canvas = document.createElement("canvas");
    let context = null;
    return {
      engine: "zxing",
      async detect(video) {
        const width = video.videoWidth;
        const height = video.videoHeight;
        if (!width || !height) return [];
        const scale = Math.min(1, 800 / width);
        const targetWidth = Math.max(1, Math.round(width * scale));
        const targetHeight = Math.max(1, Math.round(height * scale));
        if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
          canvas.width = targetWidth;
          canvas.height = targetHeight;
          context = null;
        }
        context = context || canvas.getContext("2d", { willReadFrequently: true });
        context.drawImage(video, 0, 0, targetWidth, targetHeight);
        try {
          const luminance = new ZXing.HTMLCanvasElementLuminanceSource(canvas, false);
          const bitmap = new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(luminance));
          const value = String(reader.decodeWithState(bitmap).getText() || "").trim();
          return value ? [{ rawValue: value }] : [];
        } catch (error) {
          if (error instanceof ZXing.NotFoundException) return [];
          if (ZXing.ChecksumException && error instanceof ZXing.ChecksumException) return [];
          if (ZXing.FormatException && error instanceof ZXing.FormatException) return [];
          throw error;
        }
      },
      dispose() {
        reader.reset();
        context = null;
      },
    };
  }

  async function create() {
    const Detector = nativeDetectorClass();
    if (Detector) {
      const formats = await supportedNativeFormats(Detector);
      if (formats.length) {
        try {
          return nativeAdapter(new Detector({ formats }));
        } catch (error) {
          // Fall through to the bundled decoder when the native one cannot start.
        }
      }
    }
    if (zxingClass()) {
      return zxingAdapter();
    }
    throw new Error("no_decoder");
  }

  window.CameraScan = { available, create };
})();
