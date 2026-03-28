<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';

	import Modal from '$lib/components/common/Modal.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import {
		buildSceneNote,
		getScenePresetById,
		resolveSceneNoteText,
		SCENE_NOTE_PRESETS,
		type SceneNote
	} from '$lib/utils/sceneNotes';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher<{ save: SceneNote | null }>();

	export let show = false;
	export let value: SceneNote | null = null;

	let draftPresetId: string | null = null;
	let draftTitle = '';
	let draftNote = '';
	let lastLoadedSignature = '';

	const resetDraft = (sceneNote: SceneNote | null) => {
		draftPresetId = sceneNote?.preset_id ?? null;
		draftTitle = sceneNote?.title ?? getScenePresetById(sceneNote?.preset_id)?.label ?? '';
		draftNote = sceneNote?.note ?? '';
	};

	$: if (show) {
		const signature = JSON.stringify(value ?? null);
		if (signature !== lastLoadedSignature) {
			resetDraft(value);
			lastLoadedSignature = signature;
		}
	} else {
		lastLoadedSignature = '';
	}

	$: resolvedNote = resolveSceneNoteText({
		preset_id: draftPresetId,
		note: draftNote
	});

	const handlePresetSelect = (presetId: string | null) => {
		const previousPreset = getScenePresetById(draftPresetId);
		draftPresetId = presetId;

		const nextPreset = getScenePresetById(presetId);
		if (!draftTitle || draftTitle === previousPreset?.label) {
			draftTitle = nextPreset?.label ?? '';
		}
	};

	const handleClear = () => {
		draftPresetId = null;
		draftTitle = '';
		draftNote = '';
	};

	const handleSave = () => {
		const nextSceneNote = buildSceneNote({
			preset_id: draftPresetId,
			title: draftTitle,
			note: draftNote
		});

		dispatch('save', nextSceneNote);
		show = false;
	};

	const handleDisable = () => {
		dispatch('save', null);
		show = false;
	};
</script>

<Modal bind:show size="lg">
	<div class="flex flex-col">
		<div class="flex items-center justify-between px-5 pt-4 pb-1 dark:text-gray-300">
			<div>
				<div class="text-lg font-medium">{$i18n.t('Scene Note')}</div>
				<div class="mt-1 text-sm text-gray-500 dark:text-gray-400">
					{$i18n.t(
						'Scene changes guide the next turns of this chat. They do not rewrite earlier messages.'
					)}
				</div>
			</div>

			<button
				class="self-start"
				aria-label={$i18n.t('Close')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className="size-5" />
			</button>
		</div>

		<div class="px-5 pt-4 pb-5">
			<div class="space-y-5">
				<div class="space-y-2">
					<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
						{$i18n.t('Presets')}
					</div>

					<div class="flex flex-wrap gap-2">
						{#each SCENE_NOTE_PRESETS as preset}
							<button
								type="button"
								class="rounded-full border px-3 py-1.5 text-left text-sm transition {draftPresetId ===
								preset.id
									? 'border-gray-900 bg-gray-900 text-white dark:border-gray-100 dark:bg-gray-100 dark:text-gray-900'
									: 'border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:bg-gray-50 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-gray-700 dark:hover:bg-gray-850'}"
								on:click={() => handlePresetSelect(preset.id)}
							>
								{preset.label}
							</button>
						{/each}
					</div>

					<div class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Presets prefill the scene. You can edit everything after selecting one.')}
					</div>
				</div>

				<div class="space-y-2">
					<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
						{$i18n.t('Title')}
					</div>
					<input
						class="w-full rounded-xl border border-gray-200 bg-gray-50 px-3.5 py-2 text-sm text-gray-900 outline-hidden transition focus:border-gray-400 dark:border-gray-800 dark:bg-gray-850 dark:text-gray-100 dark:focus:border-gray-600"
						bind:value={draftTitle}
						placeholder={$i18n.t('Optional scene label')}
					/>
				</div>

				<div class="space-y-2">
					<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
						{$i18n.t('Scene Steering')}
					</div>
					<Textarea
						bind:value={draftNote}
						rows={7}
						minSize={168}
						placeholder={$i18n.t(
							'Add the current setting, mood, pacing, or relational tension for this chat.'
						)}
						className="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 outline-hidden transition focus:border-gray-400 dark:border-gray-800 dark:bg-gray-850 dark:text-gray-100 dark:focus:border-gray-600"
					/>
					<div class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t(
							'Use this for the current setting and atmosphere, not for permanent persona identity.'
						)}
					</div>
				</div>

				<div class="space-y-2">
					<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
						{$i18n.t('Resolved Preview')}
					</div>
					<div
						class="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-700 dark:border-gray-800 dark:bg-gray-850 dark:text-gray-200"
					>
						{#if resolvedNote}
							<div class="whitespace-pre-wrap">{resolvedNote}</div>
						{:else}
							<div class="text-gray-500 dark:text-gray-400">
								{$i18n.t('Pick a preset, write a note, or both.')}
							</div>
						{/if}
					</div>

					<div class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t(
							'The model will be told that the user deliberately chose this scene and that it applies from this point onward.'
						)}
					</div>
				</div>
			</div>

			<div class="mt-6 flex items-center justify-between gap-3">
				<div class="flex gap-2">
					<button
						type="button"
						class="rounded-full border border-gray-200 px-3.5 py-2 text-sm font-medium text-gray-700 transition hover:border-gray-300 hover:bg-gray-50 dark:border-gray-800 dark:text-gray-200 dark:hover:border-gray-700 dark:hover:bg-gray-850"
						on:click={handleClear}
					>
						{$i18n.t('Clear')}
					</button>
					<button
						type="button"
						class="rounded-full border border-gray-200 px-3.5 py-2 text-sm font-medium text-gray-700 transition hover:border-gray-300 hover:bg-gray-50 dark:border-gray-800 dark:text-gray-200 dark:hover:border-gray-700 dark:hover:bg-gray-850"
						on:click={handleDisable}
					>
						{$i18n.t('Disable')}
					</button>
				</div>

				<div class="flex gap-2">
					<button
						type="button"
						class="rounded-full border border-gray-200 px-3.5 py-2 text-sm font-medium text-gray-700 transition hover:border-gray-300 hover:bg-gray-50 dark:border-gray-800 dark:text-gray-200 dark:hover:border-gray-700 dark:hover:bg-gray-850"
						on:click={() => {
							show = false;
						}}
					>
						{$i18n.t('Cancel')}
					</button>
					<button
						type="button"
						class="rounded-full bg-black px-4 py-2 text-sm font-medium text-white transition hover:bg-gray-900 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100"
						on:click={handleSave}
					>
						{$i18n.t('Save')}
					</button>
				</div>
			</div>
		</div>
	</div>
</Modal>
