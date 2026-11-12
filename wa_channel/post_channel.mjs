// MasRainman → WhatsApp Channel poster (free, via Baileys linked device)
//
//   node post_channel.mjs login [phone]     -> link once. With a phone number (e.g. 919876543210)
//                                             it prints an 8-letter pairing code; without, a QR.
//                                             Then prints WA_AUTH_B64 to paste into GitHub secrets.
//   node post_channel.mjs getid <inviteLink> -> prints the channel ID (…@newsletter)
//   node post_channel.mjs post               -> posts the queues below, one connection
//
//   ../output/wa_queue/        radar cards + alerts -> South India Radar Nowcast Alerts channel
//                              (WA_ALERT_CHANNEL_ID secret, or resolved from WA_ALERT_CHANNEL_LINK)
//   ../output/wa_promo_queue/  join-the-alerts-channel teaser -> main MasRainman channel
//                              (WA_CHANNEL_ID secret = the main channel, as before)
//
// SAFETY: this script only ever sends to WhatsApp CHANNEL ids (…@newsletter) it was
// configured with. It never messages groups, chats or individual numbers.

import makeWASocket, { useMultiFileAuthState, DisconnectReason, Browsers } from '@whiskeysockets/baileys';
import qrcode from 'qrcode-terminal';
import pino from 'pino';
import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';

const AUTH_DIR = process.env.WA_AUTH_DIR || './wa_auth';
const QUEUE_DIR = process.env.WA_QUEUE_DIR || '../output/wa_queue';
const PROMO_DIR = process.env.WA_PROMO_QUEUE_DIR || '../output/wa_promo_queue';
const ALERT_LINK = process.env.WA_ALERT_CHANNEL_LINK || 'https://whatsapp.com/channel/0029VbE2C5F9RZAanCxEFv0o';
const DEFAULT_LINK = ALERT_LINK;
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

function queued(dir = QUEUE_DIR) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter(f => f.endsWith('.txt')).sort().map(f => {
    const img = path.join(dir, f.replace(/\.txt$/, '.jpg'));
    return { img: fs.existsSync(img) ? img : null, txt: path.join(dir, f) };
  });
}

// One-time channel branding: re-applied only when the description or logo file changes.
const BRAND_DESC = './alert_channel_description.txt';
const BRAND_LOGO = '../assets/alert_channel_logo.jpg';
const BRAND_MARK = '../state/wa_alert_brand_applied.txt';
const FOLLOWERS = '../state/wa_followers.csv';
const brandVersion = () => [BRAND_DESC, BRAND_LOGO].map(f => fs.existsSync(f) ? fs.statSync(f).size + ':' +
  fs.readFileSync(f).reduce((h, b) => (h * 31 + b) >>> 0, 7) : '-').join('|');
const brandPending = () => fs.existsSync(BRAND_DESC) && fs.existsSync(BRAND_LOGO) &&
  (!fs.existsSync(BRAND_MARK) || fs.readFileSync(BRAND_MARK, 'utf8') !== brandVersion());

const onlyChannel = jid => {
  if (!/^\d+@newsletter$/.test(jid || '')) throw new Error(`Refusing to post: ${jid || '(empty)'} is not a WhatsApp channel id`);
  return jid;
};

async function postQueue(sock, dir, jid, label) {
  let bad = 0;
  for (const { img, txt } of queued(dir)) {
    try {
      const caption = fs.readFileSync(txt, 'utf8');
      await sock.sendMessage(jid, img ? { image: fs.readFileSync(img), caption } : { text: caption });
      console.log(`✅ ${label}: posted`, path.basename(txt));
      if (img) fs.rmSync(img); fs.rmSync(txt);
      await wait(3000);
    } catch (e) { bad++; console.log(`⚠️ ${label}: failed`, path.basename(txt), e.message); }
  }
  return bad;
}

if (mode === 'post' && !queued().length && !queued(PROMO_DIR).length && !brandPending() && process.env.WA_COUNT_DUE !== '1') {
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
    // 1) radar cards + alerts -> the alerts channel (+ one-time branding)
    let alertJid = process.env.WA_ALERT_CHANNEL_ID;
    {
      if (!alertJid) {
        const meta = await sock.newsletterMetadata('invite', ALERT_LINK.split('/').pop());
        alertJid = meta?.id;
        console.log('Alerts channel resolved from link:', meta?.thread_metadata?.name?.text || meta?.name || '');
      }
      if (alertJid === process.env.WA_CHANNEL_ID) throw new Error('Alerts channel = main channel; refusing (radar must not go to the main channel)');
      onlyChannel(alertJid);
      if (brandPending()) {
        try {   // needs the linked number to be an ADMIN of the alerts channel
          await sock.newsletterUpdateDescription(alertJid, fs.readFileSync(BRAND_DESC, 'utf8').trim());
          await sock.newsletterUpdatePicture(alertJid, fs.readFileSync(BRAND_LOGO));
          await wait(4000);
          const check = await sock.newsletterMetadata('jid', alertJid);   // verify it really changed
          const now = check?.thread_metadata?.description?.text ?? check?.description ?? '';
          const want = fs.readFileSync(BRAND_DESC, 'utf8').trim();
          if (now && now.trim() !== want) throw new Error('description did not stick (read back a different text)');
          fs.mkdirSync(path.dirname(BRAND_MARK), { recursive: true });
          fs.writeFileSync(BRAND_MARK, brandVersion());
          console.log('🎨 Alerts channel description + logo updated and verified — marketing can start');
        } catch (e) { console.log('⚠️ Branding not applied yet (is the bot number a channel admin?):', e.message); }
      }
      failed += await postQueue(sock, QUEUE_DIR, alertJid, 'Alerts channel');
    }
    // 2) promo teaser -> the main MasRainman channel
    if (queued(PROMO_DIR).length)
      failed += await postQueue(sock, PROMO_DIR, onlyChannel(process.env.WA_CHANNEL_ID), 'Main channel promo');

    // 3) follower counts for the weekly report (net count only; WhatsApp exposes no unfollow list)
    try {
      const count = async jid => {
        const m = await sock.newsletterMetadata('jid', jid);
        return m?.thread_metadata?.subscribers_count ?? m?.subscribers ?? '';
      };
      const a = await count(alertJid).catch(() => '');
      const b = process.env.WA_CHANNEL_ID ? await count(process.env.WA_CHANNEL_ID).catch(() => '') : '';
      fs.mkdirSync(path.dirname(FOLLOWERS), { recursive: true });
      if (!fs.existsSync(FOLLOWERS)) fs.writeFileSync(FOLLOWERS, 'utc,alerts,main\n');
      fs.appendFileSync(FOLLOWERS, `${new Date().toISOString().slice(0, 19)}+00:00,${a},${b}\n`);
      console.log(`👥 Followers — alerts: ${a}, main: ${b}`);
    } catch (e) { console.log('⚠️ Follower count failed:', e.message); }
  }
  await wait(5000); // flush rotated keys to disk before exit
} finally {
  sock.end();
  process.exit(failed ? 1 : 0);
}
