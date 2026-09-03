/** Clock strings and YouTube t= for the URL source window. Seconds on the wire. */

export function parseClock(text) {
    const raw = String(text || '').trim();
    if (!raw) throw new Error('empty');
    if (raw.includes('.')) throw new Error('use colons, not dots');
    if (/^\d+$/.test(raw)) return Number(raw);
    const parts = raw.split(':');
    if (parts.length < 2 || parts.length > 3) throw new Error('use mm:ss or h:mm:ss');
    const nums = parts.map((p) => {
        if (!/^\d+$/.test(p)) throw new Error('not a clock');
        return parseInt(p, 10);
    });
    let hours = 0;
    let minutes;
    let seconds;
    if (nums.length === 2) {
        [minutes, seconds] = nums;
    } else {
        [hours, minutes, seconds] = nums;
    }
    if (minutes >= 60 || seconds >= 60) throw new Error('minute/second must be < 60');
    return hours * 3600 + minutes * 60 + seconds;
}

export function parseYoutubeT(url) {
    if (!url) return null;
    const m = String(url).match(/[?&](?:t|start)=([^&#]+)/);
    if (!m) return null;
    let token = decodeURIComponent(m[1]).trim();
    const hm = token.match(/^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/i);
    if (hm && (hm[1] || hm[2] || hm[3])) {
        return (parseInt(hm[1] || '0', 10) * 3600)
            + (parseInt(hm[2] || '0', 10) * 60)
            + parseInt(hm[3] || '0', 10);
    }
    token = token.replace(/s$/i, '');
    try {
        return parseClock(token);
    } catch {
        return null;
    }
}

export function clockFromSeconds(seconds) {
    const s = Math.max(0, Math.round(Number(seconds) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
    return `${m}:${String(sec).padStart(2, '0')}`;
}
