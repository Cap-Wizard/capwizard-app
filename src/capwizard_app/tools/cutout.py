"""Person cut-out with alpha (ProRes 4444 .mov) for the "title behind the head" layer: does what CapCut's "Rimuovi sfondo" does,
so the editor doesn't have to. Uses macOS Vision person segmentation (accurate).
usage: python cutout.py <input video> <start s> <duration s> <output .mov>
Run with a Python that has pyobjc Vision + numpy + Pillow (vault.tools_python())."""
import json, os, shutil, subprocess, sys

import numpy as np
import Quartz
import Vision
from PIL import Image, ImageFilter

FF = os.environ.get("CAPWIZARD_FFMPEG") or shutil.which("ffmpeg") or os.path.expanduser("~/Desktop/VIDEO CLAUDE/720 STUDI0/tools/ffmpeg")
FP = FF + "probe" if os.path.exists(FF + "probe") else "ffprobe"


def probe(path):
    """Upright frame size and fps (ffmpeg auto-rotates iPhone clips when decoding)."""
    r = subprocess.run([FF, "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    import re
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5}).*?([\d.]+) fps", r)
    w, h, fps = int(m.group(1)), int(m.group(2)), float(m.group(3))
    rot = re.search(r"rotation of (-?[\d.]+)|rotate\s*:\s*(-?\d+)", r)
    if rot and abs(float(rot.group(1) or rot.group(2))) % 180 == 90:
        w, h = h, w
    return w, h, fps


def mask_for(rgb, req):
    h, w, _ = rgb.shape
    rgba = np.dstack([rgb, np.full((h, w), 255, np.uint8)]).tobytes()
    prov = Quartz.CGDataProviderCreateWithData(None, rgba, len(rgba), None)
    img = Quartz.CGImageCreate(w, h, 8, 32, w * 4, Quartz.CGColorSpaceCreateDeviceRGB(), Quartz.kCGImageAlphaNoneSkipLast,
                               prov, None, False, Quartz.kCGRenderingIntentDefault)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, {})
    ok, err = handler.performRequests_error_([req], None)
    buf = req.results()[0].pixelBuffer()
    Quartz.CVPixelBufferLockBaseAddress(buf, 0)
    mw, mh, bpr = Quartz.CVPixelBufferGetWidth(buf), Quartz.CVPixelBufferGetHeight(buf), Quartz.CVPixelBufferGetBytesPerRow(buf)
    raw = Quartz.CVPixelBufferGetBaseAddress(buf).as_buffer(mh * bpr)
    m = np.frombuffer(raw, np.uint8).reshape(mh, bpr)[:, :mw].copy()
    Quartz.CVPixelBufferUnlockBaseAddress(buf, 0)
    mi = Image.fromarray(m).resize((w, h), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.2))
    return np.asarray(mi)


def main(src, start, dur, out):
    w, h, fps = probe(src)
    if w > 1080:  # CapCut canvas is 1080 wide: same aspect, so it lines up exactly with the original clip
        w, h = 1080, round(h * 1080 / w / 2) * 2
    req = Vision.VNGeneratePersonSegmentationRequest.alloc().initWithCompletionHandler_(None)
    req.setQualityLevel_(Vision.VNGeneratePersonSegmentationRequestQualityLevelAccurate)
    req.setOutputPixelFormat_(1278226488)  # kCVPixelFormatType_OneComponent8 ('L008')
    dec = subprocess.Popen([FF, "-v", "error", "-ss", str(start), "-t", str(dur), "-i", src, "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    enc = subprocess.Popen([FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
                            "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-alpha_bits", "8", "-qscale:v", "8", "-vendor", "apl0", out],
                           stdin=subprocess.PIPE)
    n, fsz = 0, w * h * 3
    while True:
        b = dec.stdout.read(fsz)
        if len(b) < fsz:
            break
        rgb = np.frombuffer(b, np.uint8).reshape(h, w, 3)
        a = mask_for(rgb, req)
        enc.stdin.write(np.dstack([rgb, a]).tobytes())
        n += 1
    enc.stdin.close(); enc.wait(); dec.wait()
    print(json.dumps({"ok": enc.returncode == 0 and n > 0, "frames": n, "size": [w, h], "fps": fps, "out": out}))


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4])
