# Desktop scope compatibility

The native settings UI is implemented and locally tested, but **do not claim Settings' “Applies to” selector targets these REST calls on the inspected Hermes SDK**.

## Inspected installed source

Root: `<hermes-checkout>/apps/desktop/src/` (read only).

- `contrib/plugin.ts:366`: `ctx.rest` delegates directly to `pluginRest(pluginId, path, opts)`.
- `api/plugins.ts:72–86`: `pluginRest` passes `...profileScoped()` to `hermesApi`; `PluginRestOptions` exposes no profile/scope argument.
- `api/client.ts:583–588`: no-argument `profileScoped()` reads `$apiRequestScope.get().profile`.
- `store/profile.ts:460–462`: `$activeGatewayProfile.subscribe` sets that ambient API request profile.
- `store/settings-scope.ts:12–17,55–66`: the separate Settings override derives `$settingsRequestProfile`; changing the Settings selector does not set the ambient API request profile.
- `app/settings/plugin-settings.tsx:410,436–441`: the Settings plugin renderer knows the Settings owner, but does not pass it into the plugin render/context or key desktop pages by that owner.
- `sdk/index.ts`: no public Settings profile/scope atom or hook is exported. Reading `host.state.profile` would not fix this and the plugin never does it.

Thus active gateway A + Settings override B still sends plugin REST to A. A backend `expected_profile: A` guard cannot detect this unsupported selector override. It DOES protect a gateway-profile change between a load and a write. No plugin code can safely repair this through the currently exposed API without accessing internals, changing core, or inventing arbitrary profile routing; all were explicitly excluded.

## Current safeguards / limitations

- UI labels the **loaded backend profile** and actual provider and shows an explicit scope compatibility warning.
- It does not claim to observe the Settings selector or retarget automatically.
- Each write preflights GET, includes `expected_profile`, rejects changed response profiles, and verifies by GET readback.
- Reload invalidates earlier requests; unmount/dispose invalidates outstanding requests.
- No implicit writes, provider switching, paid API requests, catalog queries, profile/config edits, or core changes.
- Settings-selector-aware routing remains unavailable until the public SDK gives Settings pages scoped REST contexts or exports a supported Settings scope hook and REST routing API. This release supports only the explicitly labeled active-gateway-profile workflow; do not describe it as Settings-selector aware.

## Navigation once installed / enabled by the parent

1. Enable the agent plugin using the package's documented install/config workflow (backend routes must be loaded).
2. Desktop **Capabilities → Plugins → Cartesia Voice**: enable the desktop half (opt-in).
3. **Settings → Plugins → Cartesia Voice**. Direct route: `#/settings?tab=plugins&plugin=cartesia-voice`.
4. If inventory is stale: command palette → **Reload desktop plugins**.

## Local test command

`node --test tests/test_desktop.mjs`

Uses the existing React, React DOM and jsdom under the installed Hermes root, with a VM stub only for the public SDK UI primitives and a synthetic backend fixture. No installs or Hermes build. React actually mounts the page and clicks the radio/Save button; this is not a live Electron/backend integration test. Tests use synthetic UUIDs only.
