<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';

	import { DEFAULT_CAPABILITIES, WEBUI_BASE_URL } from '$lib/constants';
	import { functions, models, tools } from '$lib/stores';
	import { getFunctions } from '$lib/apis/functions';
	import { getTools } from '$lib/apis/tools';
	import { getVoices, synthesizeOpenAISpeech } from '$lib/apis/audio';
	import type { Persona } from '$lib/apis/personas';

	import ToolsSelector from '$lib/components/workspace/Models/ToolsSelector.svelte';
	import SkillsSelector from '$lib/components/workspace/Models/SkillsSelector.svelte';
	import FiltersSelector from '$lib/components/workspace/Models/FiltersSelector.svelte';
	import ActionsSelector from '$lib/components/workspace/Models/ActionsSelector.svelte';
	import Capabilities from '$lib/components/workspace/Models/Capabilities.svelte';
	import DefaultFeatures from '$lib/components/workspace/Models/DefaultFeatures.svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	export let persona: Persona | null = null;
	export let edit = false;
	export let onSubmit: (persona: Persona) => Promise<void>;

	let loading = false;
	let loaded = false;

	let id = '';
	let name = '';
	let emoji = '';
	let profileImageUrl = `${WEBUI_BASE_URL}/static/favicon.png`;
	let description = '';
	let archetype: Persona['archetype'] = 'assistant';

	let boundModelId = '';
	let useBoundModelSystemPrompt = true;
	let systemPrompt = '';
	let greeting = '';

	let voiceId = '';
	let voiceSpeed = 1;
	let availableVoices: { id: string; name: string }[] = [];
	let previewingVoice = false;

	let toolIds: string[] = [];
	let skillIds: string[] = [];
	let filterIds: string[] = [];
	let actionIds: string[] = [];
	let defaultFeatureIds: string[] = [];
	let capabilities = { ...DEFAULT_CAPABILITIES };

	const voicePreviewText = () =>
		`Hello. I'm ${name || 'this persona'}. This is my current voice preview.`;

	const fileToDataUrl = (file: File) =>
		new Promise<string>((resolve, reject) => {
			const reader = new FileReader();
			reader.onload = () => resolve(String(reader.result));
			reader.onerror = reject;
			reader.readAsDataURL(file);
		});

	const previewVoice = async () => {
		if (!voiceId) {
			toast.error($i18n.t('Choose a voice first.'));
			return;
		}

		previewingVoice = true;
		try {
			const res = await synthesizeOpenAISpeech(
				localStorage.token,
				voiceId,
				voicePreviewText(),
				undefined,
				voiceSpeed
			);
			if (!res) return;

			const blob = await res.blob();
			const url = URL.createObjectURL(blob);
			const audio = new Audio(url);
			audio.onended = () => URL.revokeObjectURL(url);
			await audio.play();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			previewingVoice = false;
		}
	};

	const submitHandler = async () => {
		if (!name.trim()) {
			toast.error($i18n.t('Persona name is required.'));
			return;
		}

		loading = true;
		try {
			await onSubmit({
				...(id ? { id } : {}),
				name: name.trim(),
				emoji: emoji.trim() || null,
				profile_image_url: profileImageUrl || null,
				description: description.trim() || null,
				archetype,
				bound_model_id: boundModelId || null,
				system_prompt: useBoundModelSystemPrompt ? null : systemPrompt,
				greeting: greeting.trim() || null,
				voice_id: voiceId || null,
				voice_speed: voiceId ? voiceSpeed : null,
				tool_ids: toolIds,
				skill_ids: skillIds,
				filter_ids: filterIds,
				action_ids: actionIds,
				default_feature_ids: defaultFeatureIds,
				capabilities,
				is_active: persona?.is_active ?? true
			});
		} finally {
			loading = false;
		}
	};

	onMount(async () => {
		if (!$tools) {
			tools.set(await getTools(localStorage.token));
		}
		if (!$functions) {
			functions.set(await getFunctions(localStorage.token));
		}

		const voices = await getVoices(localStorage.token).catch(() => null);
		availableVoices = Object.entries(voices ?? {}).map(([id, label]) => ({
			id,
			name: `${label}`
		}));

		const source = persona ?? (sessionStorage.persona ? JSON.parse(sessionStorage.persona) : null);
		if (source) {
			id = source.id ?? '';
			name = source.name ?? '';
			emoji = source.emoji ?? '';
			profileImageUrl = source.profile_image_url ?? profileImageUrl;
			description = source.description ?? '';
			archetype = source.archetype ?? 'assistant';
			boundModelId = source.bound_model_id ?? '';
			useBoundModelSystemPrompt = source.system_prompt === null || source.system_prompt === undefined;
			systemPrompt = source.system_prompt ?? '';
			greeting = source.greeting ?? '';
			voiceId = source.voice_id ?? '';
			voiceSpeed = source.voice_speed ?? 1;
			toolIds = source.tool_ids ?? [];
			skillIds = source.skill_ids ?? [];
			filterIds = source.filter_ids ?? [];
			actionIds = source.action_ids ?? [];
			defaultFeatureIds = source.default_feature_ids ?? [];
			capabilities = { ...capabilities, ...(source.capabilities ?? {}) };
		}

		sessionStorage.removeItem('persona');
		loaded = true;
	});
</script>

{#if loaded}
	<div class="mx-auto w-full max-w-4xl pb-10">
		<div class="flex items-center justify-between gap-3 pt-4">
			<div>
				<div class="text-2xl font-medium text-gray-900 dark:text-gray-100">
					{edit ? $i18n.t('Edit Persona') : $i18n.t('Create Persona')}
				</div>
				<div class="mt-1 text-sm text-gray-500">
					{$i18n.t('Existing chats keep prior behavior settings.')}
				</div>
			</div>

			<button
				class="rounded-xl border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
				on:click={() => goto('/workspace/personas')}
			>
				{$i18n.t('Back')}
			</button>
		</div>

		<div class="mt-6 grid gap-6">
			<section class="rounded-2xl border border-gray-100 p-5 dark:border-gray-800">
				<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
					{$i18n.t('Identity')}
				</div>

				<div class="mt-4 grid gap-4 md:grid-cols-[112px_1fr]">
					<div class="flex flex-col items-center gap-3">
						<div class="size-24 overflow-hidden rounded-3xl border border-gray-100 bg-gray-50 dark:border-gray-800 dark:bg-gray-900">
							<img
								class="size-full object-cover"
								src={profileImageUrl}
								alt={name || 'Persona avatar'}
							/>
						</div>

						<label class="cursor-pointer text-xs text-blue-600 dark:text-blue-400">
							<input
								class="hidden"
								type="file"
								accept="image/*"
								on:change={async (event) => {
									const file = event.currentTarget?.files?.[0];
									if (!file) return;
									profileImageUrl = await fileToDataUrl(file);
								}}
							/>
							{$i18n.t('Upload avatar')}
						</label>
					</div>

					<div class="grid gap-4 md:grid-cols-2">
						<div>
							<div class="mb-1 text-xs font-medium text-gray-500">{$i18n.t('Name')}</div>
							<input class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 dark:border-gray-800" bind:value={name} />
						</div>

						<div>
							<div class="mb-1 text-xs font-medium text-gray-500">{$i18n.t('Emoji')}</div>
							<input class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 dark:border-gray-800" bind:value={emoji} maxlength="8" />
						</div>

						<div>
							<div class="mb-1 text-xs font-medium text-gray-500">{$i18n.t('Archetype')}</div>
							<select class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 dark:border-gray-800" bind:value={archetype}>
								<option value="assistant">{$i18n.t('Assistant')}</option>
								<option value="storyteller">{$i18n.t('Storyteller')}</option>
								<option value="companion">{$i18n.t('Companion')}</option>
								<option value="coach">{$i18n.t('Coach')}</option>
							</select>
						</div>

						<div>
							<div class="mb-1 text-xs font-medium text-gray-500">
								{$i18n.t('Bound Model')}
							</div>
							<select class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 dark:border-gray-800" bind:value={boundModelId}>
								<option value="">{$i18n.t('Select a model')}</option>
								{#each $models.filter((model) => !(model?.info?.meta?.hidden ?? false)) as model}
									<option value={model.id}>{model.name}</option>
								{/each}
							</select>
						</div>

						<div class="md:col-span-2">
							<div class="mb-1 text-xs font-medium text-gray-500">
								{$i18n.t('Description')}
							</div>
							<textarea class="min-h-24 w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 dark:border-gray-800" bind:value={description} />
						</div>

						<div class="md:col-span-2">
							<div class="mb-1 text-xs font-medium text-gray-500">
								{$i18n.t('Greeting')}
							</div>
							<textarea class="min-h-24 w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 dark:border-gray-800" bind:value={greeting} />
						</div>
					</div>
				</div>
			</section>

			<section class="rounded-2xl border border-gray-100 p-5 dark:border-gray-800">
				<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
					{$i18n.t('Behavior')}
				</div>

				<div class="mt-4">
					<div class="flex items-center gap-2">
						<Checkbox
							state={useBoundModelSystemPrompt ? 'checked' : 'unchecked'}
							on:change={(event) => {
								useBoundModelSystemPrompt = event.detail === 'checked';
							}}
						/>
						<div class="text-sm">{$i18n.t('Use bound model system prompt')}</div>
					</div>

					<div class="mt-2 text-xs text-gray-500">
						{$i18n.t('Turn this off to override the model prompt. Leave the text empty to send no system prompt.')}
					</div>
				</div>

				{#if !useBoundModelSystemPrompt}
					<div class="mt-4">
						<div class="mb-1 text-xs font-medium text-gray-500">
							{$i18n.t('System Prompt')}
						</div>
						<textarea class="min-h-40 w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 dark:border-gray-800" bind:value={systemPrompt} />
					</div>
				{/if}
			</section>

			<section class="rounded-2xl border border-gray-100 p-5 dark:border-gray-800">
				<div class="flex items-center justify-between gap-3">
					<div>
						<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
							{$i18n.t('Voice')}
						</div>
						<div class="mt-1 text-xs text-gray-500">
							{$i18n.t('Playback speed is a preference. The active engine may honor, clamp, or ignore it.')}
						</div>
					</div>

					<button
						class="rounded-xl border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 disabled:opacity-50 dark:border-gray-800 dark:hover:bg-gray-900"
						disabled={previewingVoice || !voiceId}
						on:click={previewVoice}
					>
						{#if previewingVoice}
							<span class="inline-flex items-center gap-2"><Spinner className="size-4" />{$i18n.t('Previewing')}</span>
						{:else}
							{$i18n.t('Preview Voice')}
						{/if}
					</button>
				</div>

				<div class="mt-4 grid gap-4 md:grid-cols-2">
					<div>
						<div class="mb-1 text-xs font-medium text-gray-500">{$i18n.t('Voice')}</div>
						<select class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 dark:border-gray-800" bind:value={voiceId}>
							<option value="">{$i18n.t('Default')}</option>
							{#each availableVoices as voice}
								<option value={voice.id}>{voice.name}</option>
							{/each}
						</select>
					</div>

					<div>
						<div class="mb-1 flex items-center justify-between text-xs font-medium text-gray-500">
							<span>{$i18n.t('Speed')}</span>
							<span>{voiceSpeed.toFixed(2)}x</span>
						</div>
						<input class="w-full" type="range" min="0.5" max="2" step="0.05" bind:value={voiceSpeed} />
					</div>
				</div>
			</section>

			<section class="rounded-2xl border border-gray-100 p-5 dark:border-gray-800">
				<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
					{$i18n.t('Default Policy')}
				</div>

				<div class="mt-4 grid gap-5">
					<ToolsSelector tools={$tools ?? []} bind:selectedToolIds={toolIds} />
					<SkillsSelector bind:selectedSkillIds={skillIds} />
					<FiltersSelector
						filters={($functions ?? []).filter((func) => func.type === 'filter' && func.is_active)}
						bind:selectedFilterIds={filterIds}
					/>
					<ActionsSelector
						actions={($functions ?? []).filter((func) => func.type === 'action' && func.is_active)}
						bind:selectedActionIds={actionIds}
					/>
					<DefaultFeatures bind:featureIds={defaultFeatureIds} />
					<Capabilities bind:capabilities />
				</div>
			</section>

			<div class="flex justify-end">
				<button
					class="rounded-xl bg-black px-4 py-2 text-sm text-white disabled:opacity-60 dark:bg-white dark:text-black"
					disabled={loading}
					on:click={submitHandler}
				>
					{#if loading}
						<span class="inline-flex items-center gap-2"><Spinner className="size-4" />{$i18n.t('Saving')}</span>
					{:else}
						{edit ? $i18n.t('Save Persona') : $i18n.t('Create Persona')}
					{/if}
				</button>
			</div>
		</div>
	</div>
{/if}
