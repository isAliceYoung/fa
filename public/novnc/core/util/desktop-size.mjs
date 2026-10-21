// Keep a bounded remote desktop proportional to its CSS viewport when possible.
export function boundedDesktopSize(width, height, limits = { width: 1600, height: 1000 }) {
    if (![width, height, limits.width, limits.height].every(Number.isFinite) || width <= 0 || height <= 0) {
        throw new TypeError('Invalid desktop viewport');
    }
    const scale = Math.min(1, limits.width / width, limits.height / height);
    return { width: Math.max(640, Math.floor(width * scale)), height: Math.max(480, Math.floor(height * scale)) };
}
