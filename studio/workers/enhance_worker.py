"""Sharper pictures and their depth (runs inside tools/sdxl, which already has PyTorch and transformers).
In: {"images": [{"src", "hd"?, "depth"?}], "models": dir}. Out: {"images": [{"src", "hd_ok", "depth_ok",
"error"?}], "device", "took"}.

hd     Real-ESRGAN x4plus (BSD-3-Clause, Xinntao): the 512x896 SDXL-Turbo picture made 4x larger with real
       detail instead of a soft stretch. The network (RRDBNet) is written out below so no extra package
       is needed; its weights (64 MB) are downloaded once into the models folder (D:\\Jarvis on the PC).
depth  Depth Anything V2 Small (Apache-2.0): how far away every pixel is (white = near), so Jarvis can move
       a camera through the picture with near things moving more than far ones.
Each runs in tiles / at a small size, so a 6 GB card is plenty."""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

pin, pout = sys.argv[1], sys.argv[2]
job = json.load(open(pin))
ESRGAN_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"

try:
    import numpy as np
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from PIL import Image

    class RDB(nn.Module):
        def __init__(self, nf=64, gc=32):
            super().__init__()
            self.conv1 = nn.Conv2d(nf, gc, 3, 1, 1)
            self.conv2 = nn.Conv2d(nf + gc, gc, 3, 1, 1)
            self.conv3 = nn.Conv2d(nf + 2 * gc, gc, 3, 1, 1)
            self.conv4 = nn.Conv2d(nf + 3 * gc, gc, 3, 1, 1)
            self.conv5 = nn.Conv2d(nf + 4 * gc, nf, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(0.2, True)

        def forward(self, x):
            x1 = self.lrelu(self.conv1(x))
            x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
            x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
            x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
            return self.conv5(torch.cat((x, x1, x2, x3, x4), 1)) * 0.2 + x

    class RRDB(nn.Module):
        def __init__(self, nf=64, gc=32):
            super().__init__()
            self.rdb1, self.rdb2, self.rdb3 = RDB(nf, gc), RDB(nf, gc), RDB(nf, gc)

        def forward(self, x):
            return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x

    class RRDBNet(nn.Module):
        """Real-ESRGAN's generator (x4), the same layer names as its released weights."""

        def __init__(self, nf=64, nb=23, gc=32):
            super().__init__()
            self.conv_first = nn.Conv2d(3, nf, 3, 1, 1)
            self.body = nn.Sequential(*[RRDB(nf, gc) for _ in range(nb)])
            self.conv_body = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_up1 = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_up2 = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_hr = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_last = nn.Conv2d(nf, 3, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(0.2, True)

        def forward(self, x):
            feat = self.conv_first(x)
            feat = feat + self.conv_body(self.body(feat))
            feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
            feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
            return self.conv_last(self.lrelu(self.conv_hr(feat)))

    t0 = time.time()
    cuda = torch.cuda.is_available()
    dev = "cuda" if cuda else "cpu"
    half = cuda
    models = Path(job.get("models") or ".")
    want_hd = [im for im in job["images"] if im.get("hd") and not Path(im["hd"]).exists()]
    want_depth = [im for im in job["images"] if im.get("depth") and not Path(im["depth"]).exists()]
    results = {im["src"]: {"src": im["src"], "hd_ok": bool(im.get("hd")) and Path(im["hd"]).exists(),
                           "depth_ok": bool(im.get("depth")) and Path(im["depth"]).exists()} for im in job["images"]}

    def esrgan():
        w = models / "realesrgan" / "RealESRGAN_x4plus.pth"
        if not w.exists() or w.stat().st_size < 60_000_000:
            w.parent.mkdir(parents=True, exist_ok=True)
            tmp = w.with_suffix(".part")
            urllib.request.urlretrieve(ESRGAN_URL, tmp)
            os.replace(tmp, w)
        sd = torch.load(str(w), map_location="cpu", weights_only=True)
        sd = sd.get("params_ema") or sd.get("params") or sd
        net = RRDBNet()
        net.load_state_dict(sd, strict=True)
        net.eval().to(dev)
        return net.half() if half else net

    def upscale(net, img, tile=192, pad=12):
        x = torch.from_numpy(np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0).permute(2, 0, 1)[None]
        _, _, h, w = x.shape
        out = torch.zeros((1, 3, h * 4, w * 4))
        for y0 in range(0, h, tile):
            for x0 in range(0, w, tile):
                y1, x1 = min(h, y0 + tile), min(w, x0 + tile)
                ya, xa, yb, xb = max(0, y0 - pad), max(0, x0 - pad), min(h, y1 + pad), min(w, x1 + pad)
                part = x[:, :, ya:yb, xa:xb].to(dev)
                with torch.inference_mode():
                    r = net(part.half() if half else part).float().cpu()
                out[:, :, y0 * 4:y1 * 4, x0 * 4:x1 * 4] = r[:, :, (y0 - ya) * 4:(y0 - ya + y1 - y0) * 4,
                                                            (x0 - xa) * 4:(x0 - xa + x1 - x0) * 4]
        a = (out[0].clamp(0, 1).permute(1, 2, 0).numpy() * 255).round().astype(np.uint8)
        return Image.fromarray(a, "RGB")

    if want_hd:
        try:
            net = esrgan()
            for im in want_hd:
                try:
                    upscale(net, Image.open(im["src"])).save(im["hd"])
                    results[im["src"]]["hd_ok"] = True
                except Exception as e:
                    results[im["src"]]["error"] = f"upscale: {type(e).__name__}: {str(e)[:200]}"
                if cuda:
                    torch.cuda.empty_cache()
            del net
        except Exception as e:
            for im in want_hd:
                results[im["src"]]["error"] = f"upscale: {type(e).__name__}: {str(e)[:200]}"
        if cuda:
            torch.cuda.empty_cache()

    if want_depth:
        try:
            from transformers import AutoImageProcessor, AutoModelForDepthEstimation
            proc = AutoImageProcessor.from_pretrained(DEPTH_MODEL)
            model = AutoModelForDepthEstimation.from_pretrained(DEPTH_MODEL).to(dev).eval()
            for im in want_depth:
                try:
                    img = Image.open(im["src"]).convert("RGB")
                    inputs = proc(images=img, return_tensors="pt").to(dev)
                    with torch.inference_mode():
                        d = model(**inputs).predicted_depth
                    d = F.interpolate(d[:, None], size=(img.height, img.width), mode="bicubic", align_corners=False)[0, 0]
                    d = d.float().cpu().numpy()
                    lo, hi = np.percentile(d, 1), np.percentile(d, 99)
                    d = np.clip((d - lo) / max(1e-6, hi - lo), 0, 1)
                    Image.fromarray((d * 65535).astype(np.uint16)).save(im["depth"])
                    results[im["src"]]["depth_ok"] = True
                except Exception as e:
                    results[im["src"]]["error"] = f"depth: {type(e).__name__}: {str(e)[:200]}"
        except Exception as e:
            for im in want_depth:
                results[im["src"]]["error"] = f"depth: {type(e).__name__}: {str(e)[:200]}"

    json.dump({"images": list(results.values()), "device": torch.cuda.get_device_name(0) if cuda else "cpu",
               "took": round(time.time() - t0, 1)}, open(pout, "w"))
except Exception as e:
    json.dump({"error": f"{type(e).__name__}: {e}"}, open(pout, "w"))
