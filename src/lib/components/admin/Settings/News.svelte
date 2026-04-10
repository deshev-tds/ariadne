<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Switch from '$lib/components/common/Switch.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import {
		getNewsCategories,
		getNewsConfig,
		getNewsSourceRegistry,
		playLatestNews,
		runNewsDaily,
		runNewsHourly,
		updateNewsCategories,
		updateNewsConfig,
		updateNewsSourceRegistry
	} from '$lib/apis/news';

	const i18n = getContext('i18n');

	export let saveHandler: () => void = () => {};

	type NewsSource = {
		source_id: string;
		label: string;
		adapter_type: string;
		enabled: boolean;
		seed_urls: string[];
		language: string;
		region_tags: string[];
		topic_tags: string[];
	};

	type NewsCategory = {
		category_id: string;
		label: string;
		help_text: string;
		enabled: boolean;
		display_order: number;
		target_slots: number;
		assignment_terms: string[];
		preferred_source_ids: string[];
	};

	let config = null;
	let sourceRegistry: NewsSource[] = [];
	let categories: NewsCategory[] = [];
	let sourceRegistryRaw = '';
	let categoriesRaw = '';
	let sourceValidation = null;
	let categoryValidation = null;
	let sourceSemanticHash = '';
	let categorySemanticHash = '';
	let busy = false;
	let workerBusy = false;

	const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

	const parseCsv = (value: string): string[] =>
		value
			.split(',')
			.map((item) => item.trim())
			.filter(Boolean);

	const stringifyCsv = (value: string[] | undefined): string => (value ?? []).join(', ');

	const refreshRawBuffers = () => {
		sourceRegistryRaw = JSON.stringify(sourceRegistry, null, 2);
		categoriesRaw = JSON.stringify(categories, null, 2);
	};

	const load = async () => {
		busy = true;
		try {
			const [configRes, sourceRes, categoryRes] = await Promise.all([
				getNewsConfig(localStorage.token),
				getNewsSourceRegistry(localStorage.token),
				getNewsCategories(localStorage.token)
			]);
			config = configRes?.config ?? null;
			sourceRegistry = clone(sourceRes?.registry ?? []);
			categories = clone(categoryRes?.categories ?? []);
			sourceValidation = sourceRes?.validation ?? null;
			categoryValidation = categoryRes?.validation ?? null;
			sourceSemanticHash = sourceRes?.semantic_hash ?? '';
			categorySemanticHash = categoryRes?.semantic_hash ?? '';
			refreshRawBuffers();
		} catch (e) {
			toast.error(`${$i18n.t('Failed to load news settings')}: ${e}`);
		} finally {
			busy = false;
		}
	};

	const saveAll = async () => {
		if (!config) return;
		busy = true;
		try {
			const normalizedConfig = {
				NEWS_ENABLED: Boolean(config.NEWS_ENABLED),
				NEWS_ARTICLE_STORE_ROOT: config.NEWS_ARTICLE_STORE_ROOT,
				NEWS_CORPUS_ROOT: config.NEWS_CORPUS_ROOT,
				NEWS_BRIEFINGS_ROOT: config.NEWS_BRIEFINGS_ROOT,
				NEWS_ARTICLE_MODEL_ENDPOINT: config.NEWS_ARTICLE_MODEL_ENDPOINT,
				NEWS_ARTICLE_MODEL: config.NEWS_ARTICLE_MODEL,
				NEWS_BRIEF_MODEL: config.NEWS_BRIEF_MODEL,
				NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS: Number(config.NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS ?? 300),
				NEWS_BRIEF_MODEL_TIMEOUT_SECONDS: Number(config.NEWS_BRIEF_MODEL_TIMEOUT_SECONDS ?? 300),
				NEWS_TTS_VOICE_ID: config.NEWS_TTS_VOICE_ID,
				NEWS_WAKE_TIME: config.NEWS_WAKE_TIME,
				NEWS_PLAYBACK_DEVICE: config.NEWS_PLAYBACK_DEVICE
			};

			const sourcePayload = sourceRegistry.map((item) => ({
				...item,
				seed_urls: item.seed_urls ?? [],
				region_tags: item.region_tags ?? [],
				topic_tags: item.topic_tags ?? []
			}));

			const categoryPayload = categories.map((item) => ({
				...item,
				target_slots: Number(item.target_slots ?? 0),
				display_order: Number(item.display_order ?? 0),
				assignment_terms: item.assignment_terms ?? [],
				preferred_source_ids: item.preferred_source_ids ?? []
			}));

			const [sourceRes, categoryRes] = await Promise.all([
				updateNewsSourceRegistry(localStorage.token, sourcePayload),
				updateNewsCategories(localStorage.token, categoryPayload)
			]);
			await updateNewsConfig(localStorage.token, normalizedConfig);

			sourceRegistry = clone(sourceRes?.registry ?? sourceRegistry);
			categories = clone(categoryRes?.categories ?? categories);
			sourceValidation = sourceRes?.validation ?? sourceValidation;
			categoryValidation = categoryRes?.validation ?? categoryValidation;
			sourceSemanticHash = sourceRes?.semantic_hash ?? sourceSemanticHash;
			categorySemanticHash = categoryRes?.semantic_hash ?? categorySemanticHash;
			refreshRawBuffers();
			saveHandler();
		} catch (e) {
			toast.error(`${$i18n.t('Failed to save news settings')}: ${e}`);
		} finally {
			busy = false;
		}
	};

	const applyRawSources = () => {
		try {
			sourceRegistry = JSON.parse(sourceRegistryRaw);
			toast.success($i18n.t('Applied raw source JSON locally'));
		} catch (e) {
			toast.error(`${$i18n.t('Invalid source registry JSON')}: ${e}`);
		}
	};

	const applyRawCategories = () => {
		try {
			categories = JSON.parse(categoriesRaw);
			toast.success($i18n.t('Applied raw categories JSON locally'));
		} catch (e) {
			toast.error(`${$i18n.t('Invalid category JSON')}: ${e}`);
		}
	};

	const addSource = () => {
		sourceRegistry = [
			...sourceRegistry,
			{
				source_id: `rss_${Date.now()}`,
				label: 'New RSS Source',
				adapter_type: 'rss_atom',
				enabled: true,
				seed_urls: [''],
				language: 'en',
				region_tags: [],
				topic_tags: []
			}
		];
		refreshRawBuffers();
	};

	const addCategory = () => {
		categories = [
			...categories,
			{
				category_id: `category_${Date.now()}`,
				label: 'New Category',
				help_text: '',
				enabled: true,
				display_order: 0,
				target_slots: 1,
				assignment_terms: [],
				preferred_source_ids: []
			}
		];
		refreshRawBuffers();
	};

	const runWorker = async (mode: 'hourly' | 'daily' | 'play') => {
		workerBusy = true;
		try {
			if (mode === 'hourly') {
				await runNewsHourly(localStorage.token);
				toast.success($i18n.t('Hourly news worker completed'));
			} else if (mode === 'daily') {
				await runNewsDaily(localStorage.token);
				toast.success($i18n.t('Daily news briefing completed'));
			} else {
				await playLatestNews(localStorage.token);
				toast.success($i18n.t('Playback started'));
			}
		} catch (e) {
			toast.error(`${$i18n.t('News worker action failed')}: ${e}`);
		} finally {
			workerBusy = false;
		}
	};

	onMount(load);
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={saveAll}
>
	<div class="space-y-4 overflow-y-scroll scrollbar-hidden h-full">
		{#if config}
			<div>
				<div class="mb-3">
					<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('General')}</div>
					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div class="mb-2.5 flex w-full justify-between">
						<div class="self-center text-xs font-medium">{$i18n.t('Enable News Lane')}</div>
						<div class="flex items-center relative">
							<Switch bind:state={config.NEWS_ENABLED} />
						</div>
					</div>

					<div class="grid grid-cols-1 xl:grid-cols-2 gap-3">
						<label class="flex flex-col gap-1">
							<span class="text-xs font-medium">{$i18n.t('Article Store Root')}</span>
							<input class="form-input rounded-xl bg-transparent" bind:value={config.NEWS_ARTICLE_STORE_ROOT} />
						</label>
						<label class="flex flex-col gap-1">
							<span class="text-xs font-medium">{$i18n.t('Corpus Root')}</span>
							<input class="form-input rounded-xl bg-transparent" bind:value={config.NEWS_CORPUS_ROOT} />
						</label>
						<label class="flex flex-col gap-1">
							<span class="text-xs font-medium">{$i18n.t('Briefings Root')}</span>
							<input class="form-input rounded-xl bg-transparent" bind:value={config.NEWS_BRIEFINGS_ROOT} />
						</label>
						<label class="flex flex-col gap-1">
							<span class="text-xs font-medium">{$i18n.t('Wake Time')}</span>
							<input class="form-input rounded-xl bg-transparent" bind:value={config.NEWS_WAKE_TIME} />
						</label>
						<label class="flex flex-col gap-1">
							<span class="text-xs font-medium">{$i18n.t('Playback Device')}</span>
							<input class="form-input rounded-xl bg-transparent" bind:value={config.NEWS_PLAYBACK_DEVICE} />
						</label>
					</div>
				</div>
			</div>

			<div>
				<div class="mb-3">
					<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Models & Schedule')}</div>
					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />
					<div class="grid grid-cols-1 xl:grid-cols-2 gap-3">
						<label class="flex flex-col gap-1">
							<span class="text-xs font-medium">{$i18n.t('Article Model Endpoint')}</span>
							<input class="form-input rounded-xl bg-transparent" bind:value={config.NEWS_ARTICLE_MODEL_ENDPOINT} />
						</label>
						<label class="flex flex-col gap-1">
							<span class="text-xs font-medium">{$i18n.t('Article Model')}</span>
							<input class="form-input rounded-xl bg-transparent" bind:value={config.NEWS_ARTICLE_MODEL} />
						</label>
						<label class="flex flex-col gap-1">
							<span class="text-xs font-medium">{$i18n.t('Brief Model')}</span>
							<input class="form-input rounded-xl bg-transparent" bind:value={config.NEWS_BRIEF_MODEL} />
						</label>
						<label class="flex flex-col gap-1">
							<span class="text-xs font-medium">{$i18n.t('Article Model Timeout (s)')}</span>
							<input
								class="form-input rounded-xl bg-transparent"
								type="number"
								min="5"
								step="1"
								bind:value={config.NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS}
							/>
						</label>
						<label class="flex flex-col gap-1">
							<span class="text-xs font-medium">{$i18n.t('Brief Model Timeout (s)')}</span>
							<input
								class="form-input rounded-xl bg-transparent"
								type="number"
								min="5"
								step="1"
								bind:value={config.NEWS_BRIEF_MODEL_TIMEOUT_SECONDS}
							/>
						</label>
						<label class="flex flex-col gap-1">
							<span class="text-xs font-medium">{$i18n.t('Voice ID')}</span>
							<input class="form-input rounded-xl bg-transparent" bind:value={config.NEWS_TTS_VOICE_ID} />
						</label>
					</div>

					<div class="flex flex-wrap gap-2 mt-3">
						<button
							class="px-3 py-2 rounded-xl bg-gray-100 dark:bg-gray-800"
							type="button"
							disabled={workerBusy}
							on:click={() => runWorker('hourly')}
						>
							{$i18n.t('Run Hourly')}
						</button>
						<button
							class="px-3 py-2 rounded-xl bg-gray-100 dark:bg-gray-800"
							type="button"
							disabled={workerBusy}
							on:click={() => runWorker('daily')}
						>
							{$i18n.t('Run Daily')}
						</button>
						<button
							class="px-3 py-2 rounded-xl bg-gray-100 dark:bg-gray-800"
							type="button"
							disabled={workerBusy}
							on:click={() => runWorker('play')}
						>
							{$i18n.t('Play Latest')}
						</button>
					</div>
				</div>
			</div>

			<div>
				<div class="mb-3">
					<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Sources')}</div>
					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />
					<div class="text-xs text-gray-500 dark:text-gray-400 mb-2">
						{$i18n.t('Semantic hash')}: <code>{sourceSemanticHash}</code>
						{#if sourceValidation}
							<span class="ml-2">{$i18n.t('Enabled')}: {sourceValidation.enabled_source_count}/{sourceValidation.source_count}</span>
						{/if}
					</div>
					<div class="space-y-3">
						{#each sourceRegistry as source, index}
							<div class="rounded-2xl border border-gray-100 dark:border-gray-800 p-3 space-y-2">
								<div class="flex justify-between items-center gap-2">
									<div class="text-xs font-medium">{source.source_id}</div>
									<div class="flex items-center gap-2">
										<Switch bind:state={source.enabled} />
										<button
											class="text-xs text-red-500"
											type="button"
											on:click={() => {
												sourceRegistry = sourceRegistry.filter((_, itemIndex) => itemIndex !== index);
												refreshRawBuffers();
											}}
										>
											{$i18n.t('Remove')}
										</button>
									</div>
								</div>
								<div class="grid grid-cols-1 xl:grid-cols-2 gap-3">
									<label class="flex flex-col gap-1">
										<span class="text-xs">{$i18n.t('Label')}</span>
										<input class="form-input rounded-xl bg-transparent" bind:value={source.label} />
									</label>
									<label class="flex flex-col gap-1">
										<span class="text-xs">{$i18n.t('Adapter Type')}</span>
										<input class="form-input rounded-xl bg-transparent" bind:value={source.adapter_type} />
									</label>
									<label class="flex flex-col gap-1">
										<span class="text-xs">{$i18n.t('Language')}</span>
										<input class="form-input rounded-xl bg-transparent" bind:value={source.language} />
									</label>
									<label class="flex flex-col gap-1">
										<span class="text-xs">{$i18n.t('Seed URLs')}</span>
										<input
											class="form-input rounded-xl bg-transparent"
											value={stringifyCsv(source.seed_urls)}
											on:input={(event) => {
												source.seed_urls = parseCsv((event.currentTarget as HTMLInputElement).value);
												refreshRawBuffers();
											}}
										/>
									</label>
									<label class="flex flex-col gap-1">
										<span class="text-xs">{$i18n.t('Region Tags')}</span>
										<input
											class="form-input rounded-xl bg-transparent"
											value={stringifyCsv(source.region_tags)}
											on:input={(event) => {
												source.region_tags = parseCsv((event.currentTarget as HTMLInputElement).value);
												refreshRawBuffers();
											}}
										/>
									</label>
									<label class="flex flex-col gap-1">
										<span class="text-xs">{$i18n.t('Topic Tags')}</span>
										<input
											class="form-input rounded-xl bg-transparent"
											value={stringifyCsv(source.topic_tags)}
											on:input={(event) => {
												source.topic_tags = parseCsv((event.currentTarget as HTMLInputElement).value);
												refreshRawBuffers();
											}}
										/>
									</label>
								</div>
							</div>
						{/each}
					</div>
					<button class="mt-3 px-3 py-2 rounded-xl bg-gray-100 dark:bg-gray-800" type="button" on:click={addSource}>
						{$i18n.t('Add RSS/Atom Source')}
					</button>
				</div>
			</div>

			<div>
				<div class="mb-3">
					<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Categories')}</div>
					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />
					<div class="text-xs text-gray-500 dark:text-gray-400 mb-2">
						{$i18n.t('Semantic hash')}: <code>{categorySemanticHash}</code>
						{#if categoryValidation}
							<span class="ml-2">{$i18n.t('Enabled')}: {categoryValidation.enabled_category_count}/{categoryValidation.category_count}</span>
						{/if}
					</div>
					<div class="space-y-3">
						{#each categories as category, index}
							<div class="rounded-2xl border border-gray-100 dark:border-gray-800 p-3 space-y-2">
								<div class="flex justify-between items-center gap-2">
									<div class="text-xs font-medium">{category.category_id}</div>
									<div class="flex items-center gap-2">
										<Switch bind:state={category.enabled} />
										<button
											class="text-xs text-red-500"
											type="button"
											on:click={() => {
												categories = categories.filter((_, itemIndex) => itemIndex !== index);
												refreshRawBuffers();
											}}
										>
											{$i18n.t('Remove')}
										</button>
									</div>
								</div>
								<div class="grid grid-cols-1 xl:grid-cols-2 gap-3">
									<label class="flex flex-col gap-1">
										<span class="text-xs">{$i18n.t('Label')}</span>
										<input class="form-input rounded-xl bg-transparent" bind:value={category.label} />
									</label>
									<label class="flex flex-col gap-1">
										<span class="text-xs">{$i18n.t('Target Slots')}</span>
										<input type="number" class="form-input rounded-xl bg-transparent" bind:value={category.target_slots} />
									</label>
									<label class="flex flex-col gap-1">
										<span class="text-xs">{$i18n.t('Display Order')}</span>
										<input type="number" class="form-input rounded-xl bg-transparent" bind:value={category.display_order} />
									</label>
									<label class="flex flex-col gap-1 xl:col-span-2">
										<span class="text-xs">{$i18n.t('Help Text')}</span>
										<input class="form-input rounded-xl bg-transparent" bind:value={category.help_text} />
									</label>
									<label class="flex flex-col gap-1">
										<span class="text-xs">{$i18n.t('Assignment Terms')}</span>
										<input
											class="form-input rounded-xl bg-transparent"
											value={stringifyCsv(category.assignment_terms)}
											on:input={(event) => {
												category.assignment_terms = parseCsv((event.currentTarget as HTMLInputElement).value);
												refreshRawBuffers();
											}}
										/>
									</label>
									<label class="flex flex-col gap-1">
										<span class="text-xs">{$i18n.t('Preferred Sources')}</span>
										<input
											class="form-input rounded-xl bg-transparent"
											value={stringifyCsv(category.preferred_source_ids)}
											on:input={(event) => {
												category.preferred_source_ids = parseCsv((event.currentTarget as HTMLInputElement).value);
												refreshRawBuffers();
											}}
										/>
									</label>
								</div>
							</div>
						{/each}
					</div>
					<button class="mt-3 px-3 py-2 rounded-xl bg-gray-100 dark:bg-gray-800" type="button" on:click={addCategory}>
						{$i18n.t('Add Category')}
					</button>
				</div>
			</div>

			<div>
				<div class="mb-3">
					<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Advanced JSON')}</div>
					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />
					<div class="grid grid-cols-1 xl:grid-cols-2 gap-3">
						<div class="space-y-2">
							<div class="text-xs font-medium">{$i18n.t('Source Registry')}</div>
							<Textarea bind:value={sourceRegistryRaw} rows={18} />
							<button class="px-3 py-2 rounded-xl bg-gray-100 dark:bg-gray-800" type="button" on:click={applyRawSources}>
								{$i18n.t('Apply Raw Sources Locally')}
							</button>
						</div>
						<div class="space-y-2">
							<div class="text-xs font-medium">{$i18n.t('Categories')}</div>
							<Textarea bind:value={categoriesRaw} rows={18} />
							<button class="px-3 py-2 rounded-xl bg-gray-100 dark:bg-gray-800" type="button" on:click={applyRawCategories}>
								{$i18n.t('Apply Raw Categories Locally')}
							</button>
						</div>
					</div>
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end">
		<button class="px-4 py-2 rounded-xl bg-black text-white dark:bg-white dark:text-black" type="submit" disabled={busy}>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
