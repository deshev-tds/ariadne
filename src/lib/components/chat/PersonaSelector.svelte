<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import { updateUserSettings } from '$lib/apis/users';
	import { personas, settings } from '$lib/stores';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let selectedPersonaId: string | null = null;
	export let disabled = false;
	export let showSetDefault = true;

	let value = '';
	$: value = selectedPersonaId ?? '';

	const saveDefaultPersona = async () => {
		settings.set({ ...$settings, personaId: selectedPersonaId ?? null });
		await updateUserSettings(localStorage.token, { ui: $settings });
	};
</script>

<div class="flex flex-col items-start gap-1">
	<div class="flex items-center gap-2">
		<button
			type="button"
			class="rounded-xl border px-3 py-2 text-sm transition {selectedPersonaId
				? 'border-gray-200 text-gray-500 dark:border-gray-800 dark:text-gray-400'
				: 'border-gray-900 bg-gray-900 text-white dark:border-gray-200 dark:bg-gray-100 dark:text-gray-900'}"
			{disabled}
			on:click={() => {
				value = '';
				dispatch('select', null);
			}}
		>
			{$i18n.t('Direct Model')}
		</button>

		<select
			class="max-w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 text-sm dark:border-gray-800"
			bind:value
			{disabled}
			on:change={() => {
				dispatch('select', value || null);
			}}
		>
			<option value="" disabled>{$i18n.t('Choose Persona')}</option>
			{#each ($personas ?? []).filter((persona) => persona.is_active) as persona}
				<option value={persona.id}>
					{persona.emoji ? `${persona.emoji} ` : ''}{persona.name}
				</option>
			{/each}
		</select>
	</div>

	{#if showSetDefault && selectedPersonaId}
		<button
			class="ml-1 text-[0.7rem] text-gray-600 dark:text-gray-400"
			on:click={saveDefaultPersona}
		>
			{$i18n.t('Set as default')}
		</button>
	{/if}
</div>
