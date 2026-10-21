# Local noVNC additions

The original license remains in LICENSE.txt. core/rfb.js adds a public
resizeLimits setter (640..1600 x 480..1000), 300ms resize debounce when enabled,
and a firstframe event after the first completed display flush. Existing callers
without resizeLimits retain upstream resizing behavior. No private methods are
replaced at runtime. The Python GUI worker independently parses RFB fragments,
enforces the same dimensions, a single screen and a 200ms rate floor, and drops
clipboard payloads. Client-side limits are not the security boundary.
