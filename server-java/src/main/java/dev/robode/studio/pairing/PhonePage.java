package dev.robode.studio.pairing;

/** The one self-contained page a phone gets: inline CSS and JS, no external assets, JS escapes are doubled for the text block. */
final class PhonePage {
    private PhonePage() {}

    static String render(String code, long secondsLeft) {
        return TEMPLATE.replace("%%CODE%%", code).replace("%%SECS%%", Long.toString(Math.max(0, secondsLeft)))
                .replace("%%MIN%%", Long.toString(Math.max(1, (secondsLeft + 59) / 60)));
    }

    private static final String TEMPLATE = """
            <!doctype html>
            <html lang="en">
            <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <meta name="referrer" content="no-referrer">
            <title>Send a photo</title>
            <style>
            body{font-family:system-ui,sans-serif;margin:0;padding:20px;background:#fff;color:#111;max-width:32rem;margin-inline:auto}
            h1{font-size:1.4rem;margin:0 0 .5rem}
            p{font-size:1.05rem;line-height:1.4}
            button{display:block;width:100%;box-sizing:border-box;font-size:1.2rem;min-height:64px;margin:14px 0;border:0;border-radius:12px;background:#1f5fd6;color:#fff}
            button.alt{background:#e8edf7;color:#12306b}
            button:disabled{opacity:.5}
            #msg{font-size:1.1rem;min-height:1.5em;margin:12px 0}
            #msg.err{color:#b00020}
            #msg.ok{color:#146c2e}
            #thumb{display:none;max-width:100%;max-height:40vh;border-radius:8px;margin-top:8px}
            .small{color:#555;font-size:.95rem}
            @media (prefers-color-scheme:dark){body{background:#111;color:#eee}button.alt{background:#2a3350;color:#dfe6ff}.small{color:#aaa}#msg.err{color:#ff8a9b}#msg.ok{color:#7ddc9a}}
            </style>
            </head>
            <body>
            <h1>Send a photo to your computer</h1>
            <p>Lay a sheet of A4 or Letter paper flat on the floor, in view.</p>
            <button id="take" type="button">Take a photo</button>
            <button id="pick" type="button" class="alt">Choose from gallery</button>
            <button id="record" type="button" class="alt">Record a site video</button>
            <input id="file" type="file" accept="image/*" capture="environment" hidden>
            <input id="video" type="file" accept="video/*" capture="environment" hidden>
            <div id="msg" role="status" aria-live="polite"></div>
            <img id="thumb" alt="The photo you sent">
            <p id="exp" class="small">Link expires in %%MIN%% min</p>
            <script>
            (function () {
              var CODE = "%%CODE%%";
              var left = %%SECS%%;
              var MAX_SIDE = 4096;
              var $ = function (id) { return document.getElementById(id); };
              var msg = $("msg"), thumb = $("thumb"), input = $("file"), take = $("take"), pick = $("pick");
              var record = $("record"), video = $("video");
              function say(text, cls) { msg.textContent = text; msg.className = cls || ""; }
              function tick() {
                $("exp").textContent = left <= 0 ? "This link has expired." : "Link expires in " + Math.max(1, Math.ceil(left / 60)) + " min";
              }
              tick();
              setInterval(function () { if (left > 0) { left -= 1; tick(); } }, 1000);
              function busy(on) { take.disabled = on; pick.disabled = on; record.disabled = on; }
              take.addEventListener("click", function () { input.setAttribute("capture", "environment"); input.click(); });
              pick.addEventListener("click", function () { input.removeAttribute("capture"); input.click(); });

              // Same steps as the desktop: EXIF rotation applied by the decoder, long side at most 4096 px, JPEG at 0.92.
              async function normalise(file) {
                if (!window.createImageBitmap) return file;
                var bmp;
                try { bmp = await createImageBitmap(file, { imageOrientation: "from-image" }); }
                catch (e) { throw new Error("This file cannot be read as a photo"); }
                try {
                  var s = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
                  var w = Math.max(1, Math.round(bmp.width * s)), h = Math.max(1, Math.round(bmp.height * s));
                  var blob;
                  if (window.OffscreenCanvas) {
                    var oc = new OffscreenCanvas(w, h), octx = oc.getContext("2d");
                    if (!octx) throw new Error("This browser cannot prepare the photo");
                    octx.drawImage(bmp, 0, 0, w, h);
                    blob = await oc.convertToBlob({ type: "image/jpeg", quality: 0.92 });
                  } else {
                    var c = document.createElement("canvas");
                    c.width = w; c.height = h;
                    var ctx = c.getContext("2d");
                    if (!ctx) throw new Error("This browser cannot prepare the photo");
                    ctx.drawImage(bmp, 0, 0, w, h);
                    blob = await new Promise(function (resolve) { c.toBlob(resolve, "image/jpeg", 0.92); });
                  }
                  if (!blob) throw new Error("The photo could not be prepared");
                  return blob;
                } finally { if (bmp.close) bmp.close(); }
              }

              // A site video is sent unchanged, with its own Content-Type, and the upload progress is shown.
              record.addEventListener("click", function () { video.click(); });
              video.addEventListener("change", function () {
                var file = video.files && video.files[0];
                video.value = "";
                if (!file) return;
                busy(true);
                thumb.style.display = "none";
                say("Uploading\\u2026 0%");
                var xhr = new XMLHttpRequest();
                xhr.open("POST", "./" + CODE + "/video");
                xhr.setRequestHeader("Content-Type", file.type || "video/mp4");
                xhr.upload.onprogress = function (e) {
                  if (e.lengthComputable) say("Uploading\\u2026 " + Math.floor(e.loaded * 100 / e.total) + "%");
                };
                xhr.onload = function () {
                  if (xhr.status >= 200 && xhr.status < 300) say("Sent. Processing starts on your computer.", "ok");
                  else say((xhr.responseText || "").slice(0, 300) || "The video was not accepted (" + xhr.status + ")", "err");
                  busy(false);
                };
                xhr.onerror = function () { say("The upload failed. Check the connection and try again.", "err"); busy(false); };
                xhr.send(file);
              });

              input.addEventListener("change", async function () {
                var file = input.files && input.files[0];
                input.value = "";
                if (!file) return;
                busy(true);
                thumb.style.display = "none";
                say("Sending\\u2026");
                try {
                  var body = await normalise(file);
                  var res = await fetch("./" + CODE + "/photo", { method: "POST", body: body, headers: { "Content-Type": "application/octet-stream" } });
                  if (!res.ok) {
                    var text = "";
                    try { text = (await res.text()).slice(0, 300); } catch (e) {}
                    throw new Error(text || "The photo was not accepted (" + res.status + ")");
                  }
                  say("Sent. It's on your computer now.", "ok");
                  thumb.src = URL.createObjectURL(body);
                  thumb.style.display = "block";
                } catch (e) {
                  say(e && e.message ? e.message : "Something went wrong", "err");
                } finally { busy(false); }
              });
            })();
            </script>
            </body>
            </html>
            """;
}
