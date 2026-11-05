# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

People the owner hands the app to: friends and acquaintances who install a
portable folder, log in with their own Telegram account, and send files to their
own chats. The owner is the first user and the one who distributes it, so the
interface has to explain itself without a manual. Sessions are personal: each
person keeps their own accounts and recipients.

## Product Purpose

A small always-available desktop window that turns a dropped file into the right
kind of Telegram message: audio becomes a voice message, video can become a
round video note, everything else is sent as a document. Success is a file that
lands in the right chat within seconds, with no dialog to fight and no doubt
about what was sent or where.

## Positioning

One window that speaks Telegram's own media vocabulary instead of asking the
user to choose a message type: the drop target, the recipient line and the
account selector are the entire interface, and the conversion work (Opus voice,
square round video) happens behind the drop. The type follows the file: audio is
a voice note, video a round note, everything else a document.

## Operating Context

Windows desktop; the window sits on top of other work while the user drags files
out of Explorer. Files come from downloads, screen recordings, voice memos and
messengers. Sending happens often and in short bursts, sometimes several files in
a row. The app is a portable folder: no installer, no background service, no
account beyond the Telegram login the user performs in-app. Failures (no ffmpeg,
not logged in, blocked recipient, Telegram rate limit) are common enough to be
part of the normal screen, not an exception path.

## Capabilities and Constraints

- Tauri 2 window, vanilla HTML/CSS/JS front end, Rust command layer, Python +
  Telethon backend, ffmpeg required only for conversion (video, non-Opus audio).
- Actions that exist today and must keep working: log in (phone → code → 2FA),
  switch and remove accounts, choose a recipient from recent chats, refresh that
  list, send one or many files, retry failed rows, edit api_id/api_hash, read
  per-file status and errors.
- Window is small and always-on-top; 340×260 is the minimum, it ships around
  480×460.
- No bundler, no framework, no external fonts or network assets: the front end
  is plain files served from the app bundle, CSP `script-src 'self'`.
- Copy in the product is English today; interface strings are short labels.

## Brand Commitments

None established. The name VoiceDrop and the existing feature names (voice,
circles, account, to) are the only fixed vocabulary.

## Evidence on Hand

The working application itself, its Telegram output (voice notes, round video
notes, documents) and the real account/chat data it operates on. There are no
marketing claims, testimonials, benchmarks or screenshots to preserve; future
work must not invent them.

## Product Principles

1. The drop is the interface: the next file is always one gesture away.
2. Say what will happen before it happens — voice, circle, or document — and
   say what did happen after.
3. Handle rare and destructive things quietly, but never hide the recipient.
4. Every state is readable at a glance in a 480 px window, including failures.
5. Self-explanatory for someone who received the folder today.

## Accessibility & Inclusion

Keyboard reachable controls, visible focus, contrast that holds on a dark
surface, and no meaning carried by colour alone (status text as well as tint).
