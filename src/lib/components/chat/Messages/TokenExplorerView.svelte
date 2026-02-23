<script lang="ts">
	import { getContext, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let telemetry: any;
	export let onCreateBranch: Function = () => {};

	let activeIndex: number | null = null;
	let activeAltRank = 0;
	let popupX = 0;
	let popupY = 0;
	let popupElement: HTMLDivElement;

	$: tokens = Array.isArray(telemetry?.tokens) ? telemetry.tokens : [];
	$: activeToken = activeIndex !== null ? tokens[activeIndex] : null;
	$: alternatives = Array.isArray(activeToken?.alternatives) ? activeToken.alternatives : [];

	$: if (alternatives.length > 0 && activeAltRank >= alternatives.length) {
		activeAltRank = 0;
	}

	const tokenDisplay = (text: unknown) => {
		if (typeof text !== 'string') {
			return '';
		}
		if (text.length === 0) {
			return '∅';
		}
		return text;
	};

	const formatProbability = (probability: unknown) => {
		const value = Number(probability);
		if (!Number.isFinite(value) || value < 0) {
			return 'n/a';
		}

		if (value >= 0.1) {
			return `${(value * 100).toFixed(1)}%`;
		}
		if (value >= 0.001) {
			return `${(value * 100).toFixed(3)}%`;
		}
		return `${(value * 100).toExponential(1)}%`;
	};

	const openToken = (event: MouseEvent, index: number) => {
		const target = event.currentTarget as HTMLElement;
		const rect = target.getBoundingClientRect();

		activeIndex = index;
		activeAltRank = 0;
		popupX = Math.max(8, Math.min(window.innerWidth - 320, rect.left));
		popupY = Math.max(8, Math.min(window.innerHeight - 280, rect.bottom + 8));
	};

	const closePopup = () => {
		activeIndex = null;
	};

	const onDocumentPointerDown = (event: PointerEvent) => {
		if (activeIndex === null) {
			return;
		}

		const target = event.target as HTMLElement;
		if (popupElement?.contains(target) || target.closest('[data-token-chip="true"]')) {
			return;
		}
		closePopup();
	};

	if (typeof window !== 'undefined') {
		window.addEventListener('pointerdown', onDocumentPointerDown);
	}

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			window.removeEventListener('pointerdown', onDocumentPointerDown);
		}
	});
</script>

<div class="mt-2 rounded-xl border border-gray-200 dark:border-gray-800 p-3 bg-white dark:bg-gray-900">
	<div class="text-xs text-gray-500 dark:text-gray-400 mb-2">
		{$i18n.t('Token Explorer')}
	</div>

	<div class="flex flex-wrap gap-1.5">
		{#each tokens as token, index (index)}
			<button
				type="button"
				data-token-chip="true"
				class="px-1.5 py-1 rounded-md border text-xs transition bg-gray-50 border-gray-200 hover:bg-gray-100 text-gray-800 dark:bg-gray-950 dark:border-gray-700 dark:hover:bg-gray-800 dark:text-gray-200 {activeIndex === index ? 'ring-1 ring-blue-500' : ''}"
				on:mouseenter={(event) => openToken(event, index)}
				on:click={(event) => openToken(event, index)}
				aria-label={`Token ${index + 1}`}
			>
				<span class="whitespace-pre-wrap break-all">{tokenDisplay(token?.text)}</span>
			</button>
		{/each}
	</div>
</div>

{#if activeToken}
	<div
		bind:this={popupElement}
		class="fixed z-30 w-80 max-w-[calc(100vw-16px)] rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-2xl"
		style={`left: ${popupX}px; top: ${popupY}px;`}
	>
		<div class="p-3 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between gap-2">
			<div class="text-xs font-medium text-gray-600 dark:text-gray-300">
				{$i18n.t('Token')} #{(activeIndex ?? 0) + 1}
			</div>
			<button
				type="button"
				class="text-xs px-2 py-1 rounded-md border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800"
				on:click={closePopup}
			>
				{$i18n.t('Close')}
			</button>
		</div>

		<div class="max-h-64 overflow-auto p-2">
			{#if alternatives.length === 0}
				<div class="text-xs text-gray-500 dark:text-gray-400 p-2">
					{$i18n.t('No alternatives available.')}
				</div>
			{:else}
				{#each alternatives as alt (alt.rank)}
					<button
						type="button"
						class="w-full text-left p-2 rounded-lg border mb-1 transition {activeAltRank === alt.rank
							? 'border-blue-500 bg-blue-50 dark:bg-blue-950/40'
							: 'border-gray-200 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800'}"
						on:click={() => {
							activeAltRank = alt.rank;
						}}
					>
						<div class="flex items-center justify-between gap-2">
							<div class="text-xs font-medium text-gray-900 dark:text-gray-100 whitespace-pre-wrap break-all">
								{tokenDisplay(alt?.text)}
							</div>
							<div class="text-[11px] text-gray-500 dark:text-gray-400 shrink-0">
								{formatProbability(alt?.prob)}
							</div>
						</div>
					</button>
				{/each}
			{/if}
		</div>

		<div class="p-3 border-t border-gray-100 dark:border-gray-800">
			<button
				type="button"
				class="w-full px-3 py-2 rounded-lg text-sm font-medium bg-gray-900 text-white hover:bg-black dark:bg-white dark:text-gray-900 dark:hover:bg-gray-200 transition"
				on:click={() => {
					if (activeIndex === null) {
						return;
					}
					onCreateBranch({ forkIndex: activeIndex, altRank: activeAltRank });
					closePopup();
				}}
			>
				{$i18n.t('Create Branch')}
			</button>
		</div>
	</div>
{/if}
