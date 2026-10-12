// Local PCM audio with a varying envelope, for real decoding and playback without model calls.
export function audioFixture(seconds = 2.4) {
  const rate = 16000, samples = Math.floor(seconds * rate);
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(rate, 24); wav.writeUInt32LE(rate * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) {
    const t = i / rate;
    const envelope = Math.pow(Math.sin(t * Math.PI * 2.3), 2) * Math.min(1, t * 8, (seconds - t) * 8);
    wav.writeInt16LE(Math.round(Math.sin(t * Math.PI * 440) * envelope * 14000), 44 + i * 2);
  }
  return wav;
}

export function serveAudioFixture(route) {
  const body = audioFixture();
  const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || '');
  const headers = { 'Accept-Ranges': 'bytes' };
  if (range) {
    const start = Number(range[1]), end = Math.min(body.length - 1, range[2] ? Number(range[2]) : body.length - 1);
    if (start >= body.length) return route.fulfill({ status: 416, headers: { 'Content-Range': `bytes */${body.length}` } });
    headers['Content-Range'] = `bytes ${start}-${end}/${body.length}`;
    return route.fulfill({ status: 206, contentType: 'audio/wav', headers, body: body.subarray(start, end + 1) });
  }
  return route.fulfill({ contentType: 'audio/wav', headers, body });
}
