import type { Logger, LogOptions, LogErrorOptions } from 'vite';
import { createLogger, defineConfig } from 'vite';
import { sveltekit } from '@sveltejs/kit/vite';

import { viteStaticCopy } from 'vite-plugin-static-copy';

const STRICT_BUILD_WARNINGS = process.env.OWUI_STRICT_BUILD_WARNINGS === '1';
const suppressedWarningFragments = [
	'Module "node:',
	'has been externalized for browser compatibility',
	'Use of eval in',
	'is imported from external module',
	'but never used in'
];

const shouldSuppressWarningMessage = (msg: string): boolean =>
	!STRICT_BUILD_WARNINGS &&
	suppressedWarningFragments.some((fragment) => msg.includes(fragment));

const baseLogger = createLogger();
const customLogger: Logger = {
	...baseLogger,
	warn(msg: string, options?: LogOptions) {
		if (shouldSuppressWarningMessage(msg)) {
			return;
		}

		baseLogger.warn(msg, options);
	},
	warnOnce(msg: string, options?: LogOptions) {
		if (shouldSuppressWarningMessage(msg)) {
			return;
		}

		baseLogger.warnOnce(msg, options);
	},
	error(msg: string, options?: LogErrorOptions) {
		baseLogger.error(msg, options);
	}
};

const shouldSuppressRollupWarning = (warning: {
	code?: string;
	plugin?: string;
	message?: string;
}): boolean => {
	if (STRICT_BUILD_WARNINGS) {
		return false;
	}

	if (warning.code === 'UNUSED_EXTERNAL_IMPORT' || warning.code === 'EVAL') {
		return true;
	}

	return shouldSuppressWarningMessage(warning.message ?? '');
};

export default defineConfig({
	customLogger,
	plugins: [
		sveltekit(),
		viteStaticCopy({
			targets: [
				{
					src: 'node_modules/onnxruntime-web/dist/*.jsep.*',

					dest: 'wasm'
				}
			]
		})
	],
	define: {
		APP_VERSION: JSON.stringify(process.env.npm_package_version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'dev-build')
	},
	build: {
		sourcemap: true,
		rollupOptions: {
			onwarn(warning, warn) {
				if (shouldSuppressRollupWarning(warning)) {
					return;
				}

				warn(warning);
			}
		}
	},
	worker: {
		format: 'es'
	},
	esbuild: {
		pure: process.env.ENV === 'dev' ? [] : ['console.log', 'console.debug', 'console.error']
	}
});
