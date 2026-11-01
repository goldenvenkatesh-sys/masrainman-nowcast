// MasRainman → WhatsApp Channel poster (free, via Baileys linked device)
//
//   node post_channel.mjs login [phone]     -> link once. With a phone number (e.g. 919876543210)
//                                             it prints an 8-letter pairing code; without, a QR.
//                                             Then prints WA_AUTH_B64 to paste into GitHub secrets.
//   node post_channel.mjs getid <inviteLink> -> prints the channel ID (…@newsletter)
//   node post_channel.mjs post               -> posts every card queued in ../output/wa_queue/
//
// The radar cycle (masrainman/social.py) fills output/wa_queue/ once an hour with
// NN.jpg + NN.txt (caption already includes the share-the-channel footer).

import makeWASocket, { useMultiFileAuthState, DisconnectReason, Browsers } from '@whiskeysockets/baileys';
import qrcode from 'qrcode-terminal';
import pino from 'pino';
import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';

const AUTH_DIR = process.env.WA_AUTH_DIR || './wa_auth';
const QUEUE_DIR = process.env.WA_QUEUE_DIR || '../output/wa_queue';
const DEFAULT_LINK = 'https://whatsapp.com/channel/0029VaBMeqH4Y9lg93fA8w3Y';
const mode = process.argv[2] || 'post';
const wait = ms => new Promise(r => setTimeout(r, ms));

async function connect() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const sock = makeWASocket({
    auth: state,
    logger: pino({ level: 'silent' }),
    browser: Browsers.ubuntu('Chrome'),
    markOnlineOnConnect: false,
  });
  sock.ev.on('creds.update', saveCreds);
  let pairingAsked = false;
  return new Promise((resolve, reject) => {
    sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
      if (qr) {
        if (mode !== 'login') return reject(new Error('Not linked — run "node post_channel.mjs login" first'));
        const phone = (process.argv[3] || '').replace(/\D/g, '');
        if (phone && !pairingAsked) {
          pairingAsked = true;
          const code = await sock.requestPairingCode(phone);
          console.log(`\nPairing code: ${code}\nOn the phone: WhatsApp → Linked devices → Link a device → "Link with phone number instead" → enter this code.\n`);
        } else if (!phone) {
          console.log('Scan this QR: WhatsApp → Linked devices → Link a device');
          qrcode.generate(qr, { small: true });
        }
      }
      if (connection === 'open') resolve(sock);
      if (connection === 'close') {
        const code = lastDisconnect?.error?.output?.statusCode;
        if (code === DisconnectReason.restartRequired) connect().then(resolve, reject); // normal right after linking
        else reject(new Error(`WhatsApp connection closed (${code}). If 401, the device was logged out — link again.`));
      }
    });
  });
}

function queued() {
  if (!fs.existsSync(QUEUE_DIR)) return [];
  return fs.readdirSync(QUEUE_DIR).filter(f => f.endsWith('.jpg')).sort()
    .map(f => ({ img: path.join(QUEUE_DIR, f), txt: path.join(QUEUE_DIR, f.replace(/\.jpg$/, '.txt')) }));
}

if (mode === 'post' && !queued().length) {
  console.log('Nothing queued for the WhatsApp channel this cycle.');
  process.exit(0);
}

const sock = await connect();
let failed = 0;
try {
  if (mode === 'login') {
    await wait(8000); // let the first key sync finish
    console.log('✅ Linked. Session saved in', AUTH_DIR);
    const zip = 'wa_auth.zip';
    try {
      if (process.platform === 'win32')
        execSync(`powershell -NoProfile -Command "Compress-Archive -Force -Path '${AUTH_DIR}' -DestinationPath ${zip}"`);
      else execSync(`zip -qr ${zip} ${AUTH_DIR}`);
      fs.writeFileSync('WA_AUTH_B64.txt', fs.readFileSync(zip).toString('base64'));
      console.log('➡️  Paste the contents of WA_AUTH_B64.txt into the GitHub secret WA_AUTH_B64');
    } catch (e) { console.log('Could not zip the session:', e.message); }
  } else if (mode === 'getid') {
    const code = (process.argv[3] || DEFAULT_LINK).split('/').pop();
    const meta = await sock.newsletterMetadata('invite', code);
    console.log('Channel:', meta?.name || meta?.thread_metadata?.name?.text || '');
    console.log('WA_CHANNEL_ID =', meta.id);
  } else {
    const jid = process.env.WA_CHANNEL_ID;
    if (!jid?.endsWith('@newsletter')) throw new Error('Set the WA_CHANNEL_ID secret (…@newsletter)');
    for (const { img, txt } of queued()) {
      try {
        const caption = fs.existsSync(txt) ? fs.readFileSync(txt, 'utf8') : '';
        await sock.sendMessage(jid, { image: fs.readFileSync(img), caption });
        console.log('✅ Posted', path.basename(img));
        fs.rmSync(img); if (fs.existsSync(txt)) fs.rmSync(txt);
        await wait(3000);
      } catch (e) { failed++; console.log('⚠️ Failed', path.basename(img), e.message); }
    }
  }
  await wait(5000); // flush rotated keys to disk before exit
} finally {
  sock.end();
  process.exit(failed ? 1 : 0);
}
