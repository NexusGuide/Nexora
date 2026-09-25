# Third-party software

Nexora's own code is MIT-licensed (see [LICENSE](LICENSE)). The Android app
also ships the following, under their own licenses. Their full texts are in
the APK under `assets/licenses/`.

## Xray core for Android

- **AndroidLibXrayLite** `v26.9.9` — <https://github.com/2dust/AndroidLibXrayLite>
  — GNU Lesser General Public License v3.0.
- It contains **Xray-core** `v26.9.9` (commit `52a412d`) —
  <https://github.com/XTLS/Xray-core> — Mozilla Public License 2.0.
- It contains GeoIP / GeoSite data from
  <https://github.com/Loyalsoldier/v2ray-rules-dat>.

The library is not modified and not committed to this repository.
`scripts/fetch-xray-core.sh` downloads the exact release above and verifies its
SHA-256 before every build. It is loaded as a separate shared library
(`libgojni.so`), so it can be replaced with a compatible build of the same
library, as the LGPL requires; its complete source is at the link above.

Nexora uses the library's published API only. It does not contain code from
v2rayNG (GPL-3.0): the configuration generator and VPN service were written
against Xray's own configuration documentation.
