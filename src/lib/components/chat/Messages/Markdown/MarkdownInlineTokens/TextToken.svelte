<script lang="ts">
	import { fade } from 'svelte/transition';
	import type { TokenExplorerRange, TokenExplorerTextPart } from '../../../tokenExplorer';

	export let token: { raw?: string; tokenExplorerParts?: TokenExplorerTextPart[] } | null = null;
	export let done = true;
	export let tokenExplorerEnabled = false;
	export let activeTokenExplorerRange: TokenExplorerRange | null = null;
	export let onTokenExplorerTokenEnter: (
		range: TokenExplorerRange,
		event: MouseEvent
	) => void = () => {};
	export let onTokenExplorerTokenMove: (
		range: TokenExplorerRange,
		event: MouseEvent
	) => void = () => {};
	export let onTokenExplorerTokenLeave: (range: TokenExplorerRange) => void = () => {};
	export let onTokenExplorerTokenClick: (
		range: TokenExplorerRange,
		event: MouseEvent
	) => void = () => {};

	let texts: string[] = [];
	let tokenExplorerParts: TokenExplorerTextPart[] = [];
	$: texts = (token?.raw ?? '').split(' ');
	$: tokenExplorerParts = Array.isArray(token?.tokenExplorerParts) ? token.tokenExplorerParts : [];
	$: hasAnnotatedParts = tokenExplorerParts.some(
		(part) => (tokenExplorerEnabled && part?.range) || part?.branchPrefix
	);

	const handleTokenEnter = (range: TokenExplorerRange | undefined, event: MouseEvent) => {
		if (range) {
			onTokenExplorerTokenEnter(range, event);
		}
	};

	const handleTokenMove = (range: TokenExplorerRange | undefined, event: MouseEvent) => {
		if (range) {
			onTokenExplorerTokenMove(range, event);
		}
	};

	const handleTokenLeave = (range: TokenExplorerRange | undefined) => {
		if (range) {
			onTokenExplorerTokenLeave(range);
		}
	};

	const handleTokenClick = (range: TokenExplorerRange | undefined, event: MouseEvent) => {
		if (range) {
			event.preventDefault();
			event.stopPropagation();
			onTokenExplorerTokenClick(range, event);
		}
	};
</script>

{#if done}
	{#if hasAnnotatedParts}
		{#each tokenExplorerParts as part, partIdx}
			{#if part?.range}
				<!-- svelte-ignore a11y-click-events-have-key-events -->
				<!-- svelte-ignore a11y-no-static-element-interactions -->
				<span
					class="token-explorer-inline-token {activeTokenExplorerRange === part.range
						? 'token-explorer-inline-token-active'
						: ''} {part.branchPrefix ? 'token-branch-prefix' : ''}"
					on:mouseenter={(event) => handleTokenEnter(part.range, event)}
					on:mousemove={(event) => handleTokenMove(part.range, event)}
					on:mouseleave={() => handleTokenLeave(part.range)}
					on:click={(event) => handleTokenClick(part.range, event)}
				>
					{part.text}
				</span>
			{:else if part?.branchPrefix}
				<span class="token-branch-prefix">
					{part.text}
				</span>
			{:else}
				{part?.text ?? ''}
			{/if}
		{/each}
	{:else}
		{token?.raw}
	{/if}
{:else}
	{#each texts as text}
		<span class="" transition:fade={{ duration: 100 }}>
			{text}{' '}
		</span>
	{/each}
{/if}

<style>
	.token-branch-prefix {
		border-radius: 4px;
		padding: 0 1px;
		background: rgba(17, 24, 39, 0.055);
		-webkit-box-decoration-break: clone;
		box-decoration-break: clone;
	}

	.token-explorer-inline-token {
		border-radius: 4px;
		padding: 0 1px;
		transition:
			background-color 120ms ease,
			box-shadow 120ms ease,
			color 120ms ease;
	}

	:global(.dark) .token-branch-prefix {
		background: rgba(255, 255, 255, 0.08);
	}

	.token-explorer-inline-token:hover,
	.token-explorer-inline-token-active {
		background: rgba(17, 24, 39, 0.08);
		box-shadow: inset 0 -1px 0 rgba(17, 24, 39, 0.16);
	}

	:global(.dark) .token-explorer-inline-token:hover,
	:global(.dark) .token-explorer-inline-token-active {
		background: rgba(255, 255, 255, 0.11);
		box-shadow: inset 0 -1px 0 rgba(255, 255, 255, 0.22);
	}
</style>
