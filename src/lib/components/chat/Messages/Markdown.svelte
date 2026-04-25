<script>
	import { onDestroy, onMount } from 'svelte';
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
		annotateMarkdownTokensForTokenBranchPrefix,
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
	export let tokenBranchDisplayPrefixLength = 0;
	/** @type {(payload: { forkIndex: number, altRank: number }) => void} */
	export let onCreateTokenBranch = () => {};

	/** @type {any[]} */
	let tokens = [];
	/** @type {number | null} */
	let pendingUpdate = null;
	let lastParsedContent = '';
	let lastTokenExplorerEnabled = false;
	let lastTokenBranchDisplayPrefixLength = 0;
	/** @type {any} */
	let lastTokenTelemetry = null;

	/** @type {any} */
	let hoverTokenExplorerRange = null;
	/** @type {any} */
	let pinnedTokenExplorerRange = null;
	/** @type {any} */
	let activeTokenExplorerRange = null;
	/** @type {any} */
	let displayedTokenExplorerRange = null;
	let tokenExplorerClientX = 0;
	let tokenExplorerClientY = 0;
	let hoverTokenExplorerClientX = 0;
	let hoverTokenExplorerClientY = 0;
	let pinnedTokenExplorerClientX = 0;
	let pinnedTokenExplorerClientY = 0;
	let selectedAltRank = 0;
	let tokenHoverActive = false;
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
		hoverTokenExplorerRange = null;
		pinnedTokenExplorerRange = null;
		tokenHoverActive = false;
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
			if (!tokenHoverActive && !pinnedTokenExplorerRange) {
				hoverTokenExplorerRange = null;
			}
		}, 180);
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
	 * @param {MouseEvent} event
	 */
	const tokenAnchorCoordinates = (event) => {
		const target = /** @type {HTMLElement | null} */ (event.currentTarget);
		const rect = target?.getBoundingClientRect?.();
		if (rect) {
			return {
				x: rect.left + rect.width / 2,
				y: rect.bottom
			};
		}
		return eventCoordinates(event);
	};

	/**
	 * @param {any} range
	 * @param {MouseEvent} event
	 */
	const showTokenExplorerHovercard = (range, event) => {
		if (!tokenExplorerEnabled || !range || pinnedTokenExplorerRange) {
			return;
		}

		cancelHovercardClose();
		const coords = eventCoordinates(event);
		hoverTokenExplorerRange = range;
		hoverTokenExplorerClientX = coords.x;
		hoverTokenExplorerClientY = coords.y;
		selectedAltRank = range.token?.alternatives?.[0]?.rank ?? 0;
		tokenHoverActive = true;
	};

	/**
	 * @param {any} range
	 * @param {MouseEvent} event
	 */
	const moveTokenExplorerHovercard = (range, event) => {
		if (pinnedTokenExplorerRange || !hoverTokenExplorerRange || hoverTokenExplorerRange !== range) {
			return;
		}

		const coords = eventCoordinates(event);
		hoverTokenExplorerClientX = coords.x;
		hoverTokenExplorerClientY = coords.y;
	};

	const leaveTokenExplorerToken = () => {
		tokenHoverActive = false;
		scheduleHovercardClose();
	};

	/**
	 * @param {any} range
	 * @param {MouseEvent} event
	 */
	const pinTokenExplorerHovercard = (range, event) => {
		if (!tokenExplorerEnabled || !range) {
			return;
		}

		cancelHovercardClose();
		const coords = tokenAnchorCoordinates(event);
		pinnedTokenExplorerRange = range;
		pinnedTokenExplorerClientX = coords.x;
		pinnedTokenExplorerClientY = coords.y;
		hoverTokenExplorerRange = null;
		tokenHoverActive = false;
		selectedAltRank = range.token?.alternatives?.[0]?.rank ?? 0;
	};

	const clearPinnedTokenExplorerHovercard = () => {
		pinnedTokenExplorerRange = null;
	};

	const parseTokens = () => {
		const processed = replaceTokens(processResponseContent(content), model?.name, $user?.name);
		if (
			processed === lastParsedContent &&
			tokenExplorerEnabled === lastTokenExplorerEnabled &&
			tokenBranchDisplayPrefixLength === lastTokenBranchDisplayPrefixLength &&
			tokenTelemetry === lastTokenTelemetry
		) {
			return;
		}
		lastParsedContent = processed;
		lastTokenExplorerEnabled = tokenExplorerEnabled;
		lastTokenBranchDisplayPrefixLength = tokenBranchDisplayPrefixLength;
		lastTokenTelemetry = tokenTelemetry;

		const nextTokens = marked.lexer(processed);
		const ranges = tokenExplorerEnabled ? buildTokenExplorerRanges(processed, tokenTelemetry) : [];
		const annotatedTokens =
			tokenExplorerEnabled && ranges.length > 0
				? annotateMarkdownTokensForTokenExplorer(nextTokens, processed, ranges)
				: nextTokens;
		tokens =
			tokenBranchDisplayPrefixLength > 0
				? annotateMarkdownTokensForTokenBranchPrefix(
						annotatedTokens,
						processed,
						tokenBranchDisplayPrefixLength
					)
				: annotatedTokens;

		if (!tokenExplorerEnabled) {
			clearTokenExplorerHovercard();
		}
	};

	/**
	 * @param {string} content
	 * @param {boolean} tokenExplorerEnabled
	 * @param {any} tokenTelemetry
	 * @param {number} tokenBranchDisplayPrefixLength
	 */
	const updateHandler = (
		content,
		tokenExplorerEnabled,
		tokenTelemetry,
		tokenBranchDisplayPrefixLength
	) => {
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

	$: updateHandler(
		content,
		tokenExplorerEnabled,
		tokenTelemetry,
		tokenBranchDisplayPrefixLength
	);
	$: displayedTokenExplorerRange = pinnedTokenExplorerRange ?? hoverTokenExplorerRange;
	$: activeTokenExplorerRange = displayedTokenExplorerRange;
	$: tokenExplorerClientX = pinnedTokenExplorerRange
		? pinnedTokenExplorerClientX
		: hoverTokenExplorerClientX;
	$: tokenExplorerClientY = pinnedTokenExplorerRange
		? pinnedTokenExplorerClientY
		: hoverTokenExplorerClientY;

	onMount(() => {
		const handleDocumentPointerDown = (event) => {
			if (!pinnedTokenExplorerRange) {
				return;
			}

			const target = event.target;
			if (!(target instanceof Element)) {
				return;
			}

			if (
				target.closest('.token-explorer-hovercard') ||
				target.closest('.token-explorer-inline-token')
			) {
				return;
			}

			clearPinnedTokenExplorerHovercard();
		};

		const handleDocumentKeydown = (event) => {
			if (event.key === 'Escape') {
				clearPinnedTokenExplorerHovercard();
			}
		};

		document.addEventListener('pointerdown', handleDocumentPointerDown);
		document.addEventListener('keydown', handleDocumentKeydown);

		return () => {
			document.removeEventListener('pointerdown', handleDocumentPointerDown);
			document.removeEventListener('keydown', handleDocumentKeydown);
		};
	});

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
		onTokenExplorerTokenClick={pinTokenExplorerHovercard}
	/>
{/key}

<TokenExplorerHovercard
	range={displayedTokenExplorerRange}
	clientX={tokenExplorerClientX}
	clientY={tokenExplorerClientY}
	mode={pinnedTokenExplorerRange ? 'pinned' : 'hover'}
	{selectedAltRank}
	onSelectAlternative={(rank) => {
		selectedAltRank = rank;
	}}
	onCreateBranch={(payload) => {
		onCreateTokenBranch(payload);
		clearTokenExplorerHovercard();
	}}
	onClose={clearPinnedTokenExplorerHovercard}
/>
