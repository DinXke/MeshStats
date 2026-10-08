/* Firmware flashen via Web Serial: het seriële DFU-protocol van de Adafruit
   nRF52-bootloader (zoals `adafruit-nrfutil dfu serial --singlebank`).
   Alleen de app wordt vervangen; de opslag (sleutel, contacten, kanalen) blijft.

   MTDFU.readPackage(arrayBuffer) -> { manifest, bin, dat }   (DFU-zip)
   MTDFU.flash(port, pkg, onProgress)                         (port = bootloaderpoort)
   MTDFU.touch(port)                                          (1200 baud: app -> bootloader)
*/
(function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const PKT = { INIT: 1, START: 3, DATA: 4, STOP: 5 };
  const MODE_APP = 4, CHUNK = 512, PAGE = 4096;
  const ERASE_S = 0.0897, PAGE_WRITE_MS = 102.4, ACK_TIMEOUT = 1000;

  // ---- zip (alleen wat een DFU-pakket nodig heeft) --------------------------------
  async function inflate(data) {
    const ds = new DecompressionStream("deflate-raw");
    const out = new Blob([data]).stream().pipeThrough(ds);
    return new Uint8Array(await new Response(out).arrayBuffer());
  }
  async function unzip(buf) {
    const u8 = new Uint8Array(buf), dv = new DataView(buf);
    let eocd = -1;
    for (let i = u8.length - 22; i >= Math.max(0, u8.length - 65557); i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    if (eocd < 0) throw new Error("geen geldig zip-bestand");
    const n = dv.getUint16(eocd + 10, true);
    let p = dv.getUint32(eocd + 16, true);
    const files = {};
    for (let k = 0; k < n; k++) {
      const method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true);
      const nlen = dv.getUint16(p + 28, true), elen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
      const off = dv.getUint32(p + 42, true);
      const name = new TextDecoder().decode(u8.subarray(p + 46, p + 46 + nlen));
      const lnl = dv.getUint16(off + 26, true), lel = dv.getUint16(off + 28, true);
      const raw = u8.subarray(off + 30 + lnl + lel, off + 30 + lnl + lel + csize);
      files[name] = method === 0 ? raw : method === 8 ? await inflate(raw) : null;
      p += 46 + nlen + elen + clen;
    }
    return files;
  }
  async function readPackage(buf) {
    const f = await unzip(buf);
    if (!f["manifest.json"]) throw new Error("geen DFU-pakket (manifest.json ontbreekt)");
    const manifest = JSON.parse(new TextDecoder().decode(f["manifest.json"])).manifest;
    const app = manifest.application;
    if (!app || manifest.softdevice || manifest.bootloader || manifest.softdevice_bootloader)
      throw new Error("dit pakket bevat meer dan alleen de app; dat flashen we hier niet");
    const bin = f[app.bin_file], dat = f[app.dat_file];
    if (!bin || !dat) throw new Error("firmware.bin of firmware.dat ontbreekt");
    return { manifest, bin, dat };
  }

  // ---- HCI/SLIP-pakketten -------------------------------------------------------
  function crc16(data) {
    let crc = 0xffff;
    for (const b of data) {
      crc = ((crc >> 8) & 0xff) | ((crc << 8) & 0xff00);
      crc ^= b;
      crc ^= (crc & 0xff) >> 4;
      crc ^= (crc << 8) << 4;
      crc ^= ((crc & 0xff) << 4) << 1;
      crc &= 0xffff;
    }
    return crc;
  }
  const int32 = (v) => [v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, (v >>> 24) & 0xff];

  let seq = 0;
  function hci(payload) {
    seq = (seq + 1) % 8;
    const len = payload.length;
    const h = [seq | (((seq + 1) % 8) << 3) | (1 << 6) | (1 << 7), 14 | ((len & 0x0f) << 4), (len & 0x0ff0) >> 4];
    h.push((~(h[0] + h[1] + h[2]) + 1) & 0xff);
    const body = h.concat(Array.from(payload));
    const crc = crc16(body);
    body.push(crc & 0xff, (crc >> 8) & 0xff);
    const out = [0xc0];
    for (const b of body) {
      if (b === 0xc0) out.push(0xdb, 0xdc);
      else if (b === 0xdb) out.push(0xdb, 0xdd);
      else out.push(b);
    }
    out.push(0xc0);
    return new Uint8Array(out);
  }

  // ---- transport ----------------------------------------------------------------
  class Link {
    constructor(port) { this.port = port; this.buf = []; this.waiter = null; this.closed = false; }
    async open() {
      await this.port.open({ baudRate: 115200, bufferSize: 4096 });
      await sleep(100);
      await this.port.setSignals({ dataTerminalReady: false });
      await sleep(50);
      await this.port.setSignals({ dataTerminalReady: true });
      await sleep(100);
      this.writer = this.port.writable.getWriter();
      this.reader = this.port.readable.getReader();
      this.pump();
    }
    async pump() {
      try {
        for (;;) {
          const { value, done } = await this.reader.read();
          if (done) break;
          for (const b of value) this.buf.push(b);
          if (this.waiter) this.waiter();
        }
      } catch (_) { /* poort dicht */ }
    }
    async ack() {
      const t0 = Date.now();
      for (;;) {
        let ends = 0, i = 0;
        for (; i < this.buf.length; i++) if (this.buf[i] === 0xc0 && ++ends === 2) break;
        if (ends === 2) { this.buf.splice(0, i + 1); return; }
        const left = ACK_TIMEOUT - (Date.now() - t0);
        if (left <= 0) throw new Error("geen antwoord van de bootloader (timeout)");
        await new Promise((r) => { this.waiter = r; setTimeout(r, left); });
        this.waiter = null;
      }
    }
    async send(payload) { await this.writer.write(hci(payload)); await this.ack(); }
    async close() {
      try { await this.reader.cancel(); } catch (_) {}
      try { this.reader.releaseLock(); this.writer.releaseLock(); } catch (_) {}
      try { await this.port.close(); } catch (_) {}
    }
  }

  async function flash(port, pkg, onProgress) {
    const prog = onProgress || (() => {});
    const link = new Link(port);
    seq = 0;
    await link.open();
    try {
      const size = pkg.bin.length;
      prog(0, "flash wissen…");
      await link.send([...int32(PKT.START), ...int32(MODE_APP), ...int32(0), ...int32(0), ...int32(size)]);
      await sleep(Math.max(500, (Math.floor(size / PAGE) + 1) * ERASE_S * 1000));
      await link.send([...int32(PKT.INIT), ...pkg.dat, 0, 0]);
      const n = Math.ceil(size / CHUNK);
      for (let k = 0; k < n; k++) {
        await link.send([...int32(PKT.DATA), ...pkg.bin.subarray(k * CHUNK, (k + 1) * CHUNK)]);
        if (k % 8 === 0) await sleep(PAGE_WRITE_MS);
        if (k % 4 === 0 || k === n - 1) prog((k + 1) / n, `schrijven ${Math.round(((k + 1) / n) * 100)} %`);
      }
      await sleep(PAGE_WRITE_MS);
      await link.send(int32(PKT.STOP));
      prog(1, "nieuwe firmware activeren…");
    } finally {
      await link.close();
    }
  }

  // 1200 baud openen en sluiten: de app springt naar de bootloader.
  async function touch(port) {
    // Met tijdslimieten: een tracker die vastzit, mag de browser niet laten wachten.
    const within = (pr, ms) => Promise.race([Promise.resolve(pr).catch(() => {}), sleep(ms)]);
    await within(port.open({ baudRate: 1200 }), 3000);
    try { await within(port.setSignals({ dataTerminalReady: false }), 1500); } catch (_) {}
    await sleep(100);
    await within(port.close(), 2500);
  }

  window.MTDFU = { readPackage, flash, touch, crc16, hci };
})();
