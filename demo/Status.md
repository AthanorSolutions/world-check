# Project status

The project homepage is up.
<!-- PROBE id=homepage-up kind=named check=http_status url=https://example.com expect=200 ttl=1d claim="the homepage is up" -->

The release config is the reviewed version.
<!-- PROBE id=release-config kind=named check=file_sha256 path=release.json expect=91d784237ae4e3fa3358feaa643ca345b83c431a6674332a0bcc6b0658fee1fd ttl=7d claim="release.json is the reviewed version" -->

Release 1.4 is marked stable.
<!-- PROBE id=release-stable kind=named check=json_path_equals file=release.json path=channels.stable expect=1.4.0 ttl=12h claim="1.4.0 is the stable release" -->

The launch email went to the beta list.
<!-- PROBE id=launch-email kind=claude query="Gmail sent: subject:launch to:beta@ newer_than:14d" ttl=14d claim="the launch email went out" -->

The status page shows all systems green.
<!-- PROBE id=status-green kind=human test="open the status page while logged in; banner reads All systems operational" ttl=3d claim="status page is green" -->
