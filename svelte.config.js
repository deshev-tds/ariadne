import adapter from '@sveltejs/adapter-static';
import * as child_process from 'node:child_process';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import fs from 'node:fs';

const STRICT_BUILD_WARNINGS = process.env.OWUI_STRICT_BUILD_WARNINGS === '1';
const IGNORED_SVELTE_WARNING_CODES = new Set([
	'css_unused_selector',
	'element_invalid_self_closing_tag',
	'export_let_unused',
	'node_invalid_placement_ssr',
	'reactive_declaration_module_script_dependency'
]);

const shouldIgnoreSvelteWarning = (warning) => {
	if (STRICT_BUILD_WARNINGS) {
		return false;
	}

	return (
		IGNORED_SVELTE_WARNING_CODES.has(warning.code) || warning.code?.startsWith('a11y_')
	);
};

/** @type {import('@sveltejs/kit').Config} */
const config = {
	// Consult https://kit.svelte.dev/docs/integrations#preprocessors
	// for more information about preprocessors
	preprocess: vitePreprocess(),
	kit: {
		// adapter-auto only supports some environments, see https://kit.svelte.dev/docs/adapter-auto for a list.
		// If your environment is not supported or you settled on a specific environment, switch out the adapter.
		// See https://kit.svelte.dev/docs/adapters for more information about adapters.
		adapter: adapter({
			pages: 'build',
			assets: 'build',
			fallback: 'index.html'
		}),
		// poll for new version name every 60 seconds (to trigger reload mechanic in +layout.svelte)
		version: {
			name: (() => {
				try {
					return child_process.execSync('git rev-parse HEAD').toString().trim();
				} catch {
					// if git is not available, fallback to package.json version
					// or current timestamp
					try {
						return (
							JSON.parse(fs.readFileSync(new URL('./package.json', import.meta.url), 'utf8'))
								?.version || Date.now().toString()
						);
					} catch {
						return Date.now().toString();
					}
				}
			})(),
			pollInterval: 60000
		}
	},
	vitePlugin: {
		experimental: {
			disableSvelteResolveWarnings: !STRICT_BUILD_WARNINGS
		},
		// inspector: {
		// 	toggleKeyCombo: 'meta-shift', // Key combination to open the inspector
		// 	holdMode: false, // Enable or disable hold mode
		// 	showToggleButton: 'always', // Show toggle button ('always', 'active', 'never')
		// 	toggleButtonPos: 'bottom-right' // Position of the toggle button
		// }
	},
	onwarn: (warning, handler) => {
		if (shouldIgnoreSvelteWarning(warning)) return;

		handler(warning);
	}
};

export default config;
