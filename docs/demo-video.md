# Recording the demo video

The README video is generated from the running application, not edited by hand, so it can be
re-recorded whenever the UI changes.

```bash
make sandbox-image                 # the walkthrough shows tests running in the sandbox
cd apps/web
npm run build
npm run demo:record                # Playwright: starts the API (demo mode) + web, drives the UI
```

`demo/record-demo.spec.ts` contains the script, including the on-screen captions. Playwright
writes `demo-output/**/video.webm` (gitignored). Convert it for the README:

```bash
V=apps/web/demo-output/record-demo-Orchestra-AI-walkthrough/video.webm
# full-quality MP4 (linked from the README)
ffmpeg -i "$V" -c:v libx264 -preset slow -crf 26 -pix_fmt yuv420p -movflags +faststart -an docs/media/orchestra-demo.mp4
# inline GIF, 1.25x speed, kept under ~10 MB so GitHub renders it
ffmpeg -i "$V" -vf "setpts=PTS/1.25,fps=7,scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" -loop 0 docs/media/orchestra-demo.gif
```

GitHub does not play repository `.mp4` files inline in a README, which is why the README
shows the GIF and links it to the MP4.
