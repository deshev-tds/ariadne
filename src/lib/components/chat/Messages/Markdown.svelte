<script>
	import { onDestroy } from 'svelte';
	import { marked } from 'marked';
	import { replaceTokens, processResponseContent } from '$lib/utils';
	import { user } from '$lib/stores';

	import markedExtension from '$lib/utils/marked/extension';
	import markedKatexExtension from '$lib/utils/marked/katex-extension';
	import { disableSingleTilde } from '$lib/utils/marked/strikethrough-extension';
	import { mentionExtension } from '$lib/utils/marked/mention-extension';
	import colonFenceExtension from '$lib/utils/marked/colon-fence-extension';

	import MarkdownTokens from './Markdown/MarkdownTokens.svelte';
	import TokenExplorerHovercard from './TokenExplorerHovercard.svelte';
	import footnoteExtension from '$lib/utils/marked/footnote-extension';
	import citationExtension from '$lib/utils/marked/citation-extension';
	import {
		annotateMarkdownTokensForTokenExplorer,
		buildTokenExplorerRanges
	} from '../tokenExplorer';

	export let id = '';
	/** @type {string} */
	export let content = '';
	export let done = true;
	/** @type {any} */
	export let model = null;
	export let save = false;
	export let preview = false;

	export let paragraphTag = 'p';
	export let editCodeBlock = true;
	export let topPadding = false;

	/** @type {any[]} */
	export let sourceIds = [];

	export let onSave = () => {};
	export let onUpdate = () => {};

	export let onPreview = () => {};

	export let onSourceClick = () => {};
	export let onTaskClick = () => {};

	export let tokenExplorerEnabled = false;
	/** @type {any} */
	export let tokenTelemetry = null;
	/** @type {(payload: { forkIndex: number, altRank: number }) => void} */
	export let onCreateTokenBranch = () => {};

	/** @type {any[]} */
	let tokens = [];
	/** @type {number | null} */
	let pendingUpdate = null;
	let lastParsedContent = '';
	let lastTokenExplorerEnabled = false;
	/** @type {any} */
	let lastTokenTelemetry = null;

	/** @type {any} */
	let activeTokenExplorerRange = null;
	let tokenExplorerClientX = 0;
	let tokenExplorerClientY = 0;
	let selectedAltRank = 0;
	let tokenHoverActive = false;
	let hovercardHoverActive = false;
	/** @type {ReturnType<typeof setTimeout> | null} */
	let hovercardCloseTimeout = null;

	const options = {
		throwOnError: false,
		breaks: true
	};

	marked.use(markedKatexExtension(options));
	marked.use(markedExtension(options));
	marked.use(citationExtension(options));
	marked.use(footnoteExtension(options));
	marked.use(colonFenceExtension(options));
	marked.use(disableSingleTilde);
	marked.use({
		extensions: [
			mentionExtension({ triggerChar: '@' }),
			mentionExtension({ triggerChar: '#' }),
			mentionExtension({ triggerChar: '$' })
		]
	});

	const clearTokenExplorerHovercard = () => {
		activeTokenExplorerRange = null;
		tokenHoverActive = false;
		hovercardHoverActive = false;
	};

	const cancelHovercardClose = () => {
		if (hovercardCloseTimeout) {
			clearTimeout(hovercardCloseTimeout);
			hovercardCloseTimeout = null;
		}
	};

	const scheduleHovercardClose = () => {
		cancelHovercardClose();
		hovercardCloseTimeout = setTimeout(() => {
			if (!tokenHoverActive && !hovercardHoverActive) {
				clearTokenExplorerHovercard();
			}
		}, 120);
	};

	/**
	 * @param {MouseEvent} event
	 */
	const eventCoordinates = (event) => {
		const target = /** @type {HTMLElement | null} */ (event.currentTarget);
		const rect = target?.getBoundingClientRect?.();
		return {
			x: Number.isFinite(event.clientX)
				? event.clientX
				: rect
					? rect.left + rect.width / 2
					: window.innerWidth / 2,
			y: Number.isFinite(event.clientY)
				? event.clientY
				: rect
					? rect.bottom
					: window.innerHeight / 2
		};
	};

	/**
	 * @param {any} range
	 * @param {MouseEvent} event
	 */
	const showTokenExplorerHovercard = (range, event) => {
		if (!tokenExplorerEnabled || !range) {
			return;
		}

		cancelHovercardClose();
		const coords = eventCoordinates(event);
		activeTokenExplorerRange = range;
		tokenExplorerClientX = coords.x;
		tokenExplorerClientY = coords.y;
		selectedAltRank = range.token?.alternatives?.[0]?.rank ?? 0;
		tokenHoverActive = true;
	};

	/**
	 * @param {any} range
	 * @param {MouseEvent} event
	 */
	const moveTokenExplorerHovercard = (range, event) => {
		if (!activeTokenExplorerRange || activeTokenExplorerRange !== range) {
			return;
		}

		const coords = eventCoordinates(event);
		tokenExplorerClientX = coords.x;
		tokenExplorerClientY = coords.y;
	};

	const leaveTokenExplorerToken = () => {
		tokenHoverActive = false;
		scheduleHovercardClose();
	};

	const parseTokens = () => {
		const processed = replaceTokens(processResponseContent(content), model?.name, $user?.name);
		if (
			processed === lastParsedContent &&
			tokenExplorerEnabled === lastTokenExplorerEnabled &&
			tokenTelemetry === lastTokenTelemetry
		) {
			return;
		}
		lastParsedContent = processed;
		lastTokenExplorerEnabled = tokenExplorerEnabled;
		lastTokenTelemetry = tokenTelemetry;

		const nextTokens = marked.lexer(processed);
		const ranges = tokenExplorerEnabled ? buildTokenExplorerRanges(processed, tokenTelemetry) : [];
		tokens =
			tokenExplorerEnabled && ranges.length > 0
				? annotateMarkdownTokensForTokenExplorer(nextTokens, processed, ranges)
				: nextTokens;

		if (!tokenExplorerEnabled) {
			clearTokenExplorerHovercard();
		}
	};

	/**
	 * @param {string} content
	 * @param {boolean} tokenExplorerEnabled
	 * @param {any} tokenTelemetry
	 */
	const updateHandler = (content, tokenExplorerEnabled, tokenTelemetry) => {
		if (content) {
			if (done) {
				if (pendingUpdate !== null) {
					cancelAnimationFrame(pendingUpdate);
				}
				pendingUpdate = null;
				parseTokens();
			} else if (!pendingUpdate) {
				pendingUpdate = requestAnimationFrame(() => {
					pendingUpdate = null;
					parseTokens();
				});
			}
		}
	};

	$: updateHandler(content, tokenExplorerEnabled, tokenTelemetry);

	// Throttle parsing to once per animation frame while streaming
	onDestroy(() => {
		if (pendingUpdate !== null) {
			cancelAnimationFrame(pendingUpdate);
		}
		cancelHovercardClose();
	});
</script>

{#key id}
	<MarkdownTokens
		{tokens}
		{id}
		{done}
		{save}
		{preview}
		{paragraphTag}
		{editCodeBlock}
		{sourceIds}
		{topPadding}
		{onTaskClick}
		{onSourceClick}
		{onSave}
		{onUpdate}
		{onPreview}
		{tokenExplorerEnabled}
		{activeTokenExplorerRange}
		onTokenExplorerTokenEnter={showTokenExplorerHovercard}
		onTokenExplorerTokenMove={moveTokenExplorerHovercard}
		onTokenExplorerTokenLeave={leaveTokenExplorerToken}
	/>
{/key}

<TokenExplorerHovercard
	range={activeTokenExplorerRange}
	clientX={tokenExplorerClientX}
	clientY={tokenExplorerClientY}
	{selectedAltRank}
	onSelectAlternative={(rank) => {
		selectedAltRank = rank;
	}}
	onCreateBranch={(payload) => {
		onCreateTokenBranch(payload);
		clearTokenExplorerHovercard();
	}}
	onHovercardEnter={() => {
		cancelHovercardClose();
		hovercardHoverActive = true;
	}}
	onHovercardLeave={() => {
		hovercardHoverActive = false;
		scheduleHovercardClose();
	}}
/>
