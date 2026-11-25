"""How well pictures match words, with OpenCLIP's ViT-B/32 (LAION-2B, MIT licence) loaded through transformers
(already in tools/sdxl, which SDXL itself needs). Runs inside tools/sdxl. In: {"images": [path], "texts": [str]}.
Out: {"sims": [[cosine of image i with text j]], "device", "took"}. About 600 MB, downloaded once."""
import json
import sys
import time

pin, pout = sys.argv[1], sys.argv[2]
job = json.load(open(pin))
MODEL = "laion/CLIP-ViT-B-32-laion2B-s34B-b79K"

try:
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor
    t0 = time.time()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CLIPModel.from_pretrained(MODEL).to(device).eval()
    proc = CLIPProcessor.from_pretrained(MODEL)
    images = [Image.open(p).convert("RGB") for p in job["images"]]
    with torch.inference_mode():
        ti = proc(text=list(job["texts"]), return_tensors="pt", padding=True, truncation=True).to(device)
        tv = model.get_text_features(**ti)
        sims = []
        for k in range(0, len(images), 8):
            iv = model.get_image_features(**proc(images=images[k:k + 8], return_tensors="pt").to(device))
            iv = iv / iv.norm(dim=-1, keepdim=True)
            t = tv / tv.norm(dim=-1, keepdim=True)
            sims += (iv @ t.T).float().cpu().tolist()
    json.dump({"sims": [[round(x, 4) for x in row] for row in sims], "device": device,
               "took": round(time.time() - t0, 1)}, open(pout, "w"))
except Exception as e:
    json.dump({"error": f"{type(e).__name__}: {e}"}, open(pout, "w"))
