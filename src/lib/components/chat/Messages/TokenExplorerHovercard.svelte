<script lang="ts">
	import { tick } from 'svelte';
	import {
		formatTokenExplorerLogprob,
		formatTokenExplorerProbability,
		type TokenExplorerRange
	} from '../tokenExplorer';

	export let range: TokenExplorerRange | null = null;
	export let clientX = 0;
	export let clientY = 0;
	export let selectedAltRank = 0;
	export let onSelectAlternative: (rank: number) => void = () => {};
	export let onCreateBranch: (payload: { forkIndex: number; altRank: number }) => void = () => {};
	export let onHovercardEnter: () => void = () => {};
	export let onHovercardLeave: () => void = () => {};

	let hovercardElement: HTMLDivElement;
	let left = 0;
	let top = 0;

	$: token = range?.token ?? null;
	$: alternatives = Array.isArray(token?.alternatives) ? token.alternatives.slice(0, 10) : [];

	const tokenDisplay = (text: unknown) => {
		if (typeof text !== 'string') {
			return '';
		}
		return text.length > 0 ? text : '(empty)';
	};

	const updatePosition = async () => {
		if (!range) {
			return;
		}

		await tick();

		const pad = 12;
		const rect = hovercardElement?.getBoundingClientRect();
		const width = rect?.width ?? 320;
		const height = rect?.height ?? 260;

		let nextLeft = clientX + 14;
		let nextTop = clientY + 16;

		if (nextLeft + width + pad > window.innerWidth) {
			nextLeft = clientX - width - 14;
		}
		if (nextTop + height + pad > window.innerHeight) {
			nextTop = clientY - height - 14;
		}

		left = Math.max(pad, nextLeft);
		top = Math.max(pad, nextTop);
	};

	$: if (range || clientX || clientY) {
		updatePosition();
	}
</script>

{#if range && token}
	<div
		bind:this={hovercardElement}
		role="dialog"
		aria-label="Token Explorer"
		tabindex="-1"
		class="fixed z-50 w-80 max-w-[calc(100vw-24px)] rounded-xl border border-gray-200 bg-white/95 p-2.5 text-gray-900 shadow-2xl backdrop-blur-sm dark:border-gray-700 dark:bg-gray-900/95 dark:text-gray-100"
		style={`left: ${left}px; top: ${top}px;`}
		on:mouseenter={() => onHovercardEnter()}
		on:mouseleave={() => onHovercardLeave()}
	>
		<div class="mb-2 flex items-start justify-between gap-3">
			<div class="min-w-0">
				<div class="text-xs font-medium text-gray-600 dark:text-gray-300">
					Token #{range.tokenIndex + 1}
				</div>
				<div class="mt-0.5 line-clamp-2 whitespace-pre-wrap break-words text-sm font-semibold">
					{tokenDisplay(token.text)}
				</div>
			</div>
			<div class="shrink-0 text-right text-[11px] text-gray-500 dark:text-gray-400">
				<div>p {formatTokenExplorerProbability(token.prob)}</div>
				<div>lp {formatTokenExplorerLogprob(token.logprob)}</div>
			</div>
		</div>

		<div class="max-h-56 overflow-y-auto pr-1">
			{#if alternatives.length === 0}
				<div
					class="rounded-lg border border-gray-100 px-2 py-1.5 text-xs text-gray-500 dark:border-gray-800 dark:text-gray-400"
				>
					No alternatives available.
				</div>
			{:else}
				<div class="flex flex-col gap-1">
					{#each alternatives as alt (alt.rank)}
						<button
							type="button"
							class="w-full rounded-lg border px-2 py-1.5 text-left transition {selectedAltRank ===
							alt.rank
								? 'border-blue-500 bg-blue-50 dark:bg-blue-950/40'
								: 'border-gray-100 hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-800'}"
							on:click={() => onSelectAlternative(alt.rank)}
						>
							<div class="flex items-start justify-between gap-2">
								<div class="min-w-0 whitespace-pre-wrap break-words text-xs font-medium">
									{tokenDisplay(alt.text)}
								</div>
								<div class="shrink-0 text-right text-[11px] text-gray-500 dark:text-gray-400">
									<div>{formatTokenExplorerProbability(alt.prob)}</div>
									<div>{formatTokenExplorerLogprob(alt.logprob)}</div>
								</div>
							</div>
						</button>
					{/each}
				</div>
			{/if}
		</div>

		<div class="mt-2 border-t border-gray-100 pt-2 dark:border-gray-800">
			<button
				type="button"
				class="w-full rounded-lg bg-gray-900 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-black disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-200"
				disabled={alternatives.length === 0}
				on:click={() => onCreateBranch({ forkIndex: range.tokenIndex, altRank: selectedAltRank })}
			>
				Create Branch
			</button>
		</div>
	</div>
{/if}
