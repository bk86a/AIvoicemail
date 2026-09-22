# Carriers

v0.1 supports IP-authenticated SIP trunks over UDP: the carrier sends calls to your server's public
address and aivoicemail accepts SIP only from the carrier's published ranges. Registration
(username/password) trunks are planned for v0.2.

## DTMF (menu keys)

Enable **telephone-event (RFC 2833/4733 DTMF)** on the trunk at the carrier. Asterisk is configured
with `dtmf_mode = auto`: when the offer includes a `telephone-event` payload type it uses that;
otherwise it falls back to detecting DTMF in-band from the audio itself. In-band detection works but
is less robust on compressed mobile calls and low-bitrate codecs, so prefer telephone-event whenever
the carrier offers it.

## DIDWW

1. Create an inbound trunk of type SIP with destination `<public_ip>:5060`, transport UDP.
2. Enable telephone-event (RFC 2833/4733 DTMF) on the trunk. Without it DIDWW offers PCMA only;
   Asterisk (`dtmf_mode = auto`) then falls back to in-band detection, which works but is less robust
   on compressed mobile calls.
3. Route your numbers (DIDs) to the trunk. DIDWW delivers the called number and the caller ID in
   E.164 without `+`; put the DID in the config exactly like that (`did = "3220000001"`).
4. Use DIDWW's signalling and media ranges in the config:

   ```toml
   [trunk]
   provider = "didww"
   signalling_ranges = ["46.19.208.0/21", "185.238.172.0/22"]
   media_ranges      = ["46.19.208.0/21", "185.238.172.0/22"]
   ```

   Check DIDWW's documentation for the current list before going live and re-run
   `./aivm render-prompts` (or `generate`) and the firewall installation after changes.
5. Call the number: the log line `<id> <line> <outcome>` appears in `docker compose logs worker`.

## Other carriers (community-tested)

None confirmed yet. Any carrier that can deliver inbound calls by IP to UDP 5060 with G.711 (A-law
or mu-law) should work. To add one, open a pull request with: trunk settings, signalling and media
ranges (with a link to the carrier's documentation), called-number and caller-ID format, and whether
telephone-event is offered.
