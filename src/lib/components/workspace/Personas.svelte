<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';

	import { personas, models } from '$lib/stores';
	import {
		duplicatePersona,
		getPersonas,
		togglePersona,
		type Persona
	} from '$lib/apis/personas';

	import Plus from '$lib/components/icons/Plus.svelte';
	import DocumentDuplicate from '$lib/components/icons/DocumentDuplicate.svelte';

	const i18n = getContext('i18n');

	let query = '';
	let loading = false;

	const refresh = async () => {
		loading = true;
		try {
			personas.set(await getPersonas(localStorage.token));
		} finally {
			loading = false;
		}
	};

	const createChatWithPersona = async (personaId: string) => {
		await goto(`/?persona=${encodeURIComponent(personaId)}`);
	};

	const createFromExisting = async (persona: Persona) => {
		const copy = await duplicatePersona(localStorage.token, persona.id ?? '').catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (copy) {
			await refresh();
			toast.success($i18n.t('Persona duplicated.'));
		}
	};

	const toggleStatus = async (persona: Persona) => {
		const updated = await togglePersona(localStorage.token, persona.id ?? '').catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (updated) {
			await refresh();
		}
	};

	$: filteredPersonas = ($personas ?? []).filter((persona: Persona) => {
		const haystack = `${persona.name ?? ''} ${persona.description ?? ''} ${persona.archetype ?? ''}`.toLowerCase();
		return haystack.includes(query.toLowerCase());
	});

	onMount(refresh);
</script>

<div class="pb-8">
	<div class="flex items-center justify-between gap-3 pt-4">
		<div>
			<div class="text-2xl font-medium text-gray-900 dark:text-gray-100">
				{$i18n.t('Personas')}
			</div>
			<div class="mt-1 text-sm text-gray-500">
				{$i18n.t('Identity updates live. Behavior and defaults are pinned to each chat.')}
			</div>
		</div>

		<button
			class="inline-flex items-center gap-2 rounded-xl bg-black px-4 py-2 text-sm text-white dark:bg-white dark:text-black"
			on:click={() => goto('/workspace/personas/create')}
		>
			<Plus className="size-4" />
			{$i18n.t('Create Persona')}
		</button>
	</div>

	<div class="mt-5">
		<input
			class="w-full rounded-2xl border border-gray-200 bg-transparent px-4 py-3 dark:border-gray-800"
			placeholder={$i18n.t('Search personas')}
			bind:value={query}
		/>
	</div>

	<div class="mt-5 grid gap-4">
		{#if filteredPersonas.length === 0 && !loading}
			<div class="rounded-2xl border border-dashed border-gray-200 px-5 py-10 text-center text-sm text-gray-500 dark:border-gray-800">
				{$i18n.t('No personas yet.')}
			</div>
		{/if}

		{#each filteredPersonas as persona}
			<div class="rounded-2xl border border-gray-100 p-4 dark:border-gray-800">
				<div class="flex items-start justify-between gap-4">
					<div class="flex min-w-0 items-start gap-3">
						<div class="size-12 shrink-0 overflow-hidden rounded-2xl border border-gray-100 bg-gray-50 dark:border-gray-800 dark:bg-gray-900">
							<img class="size-full object-cover" src={persona.profile_image_url ?? '/static/favicon.png'} alt={persona.name} />
						</div>

						<div class="min-w-0">
							<div class="flex items-center gap-2">
								<span class="text-lg">{persona.emoji ?? '🙂'}</span>
								<div class="truncate text-base font-medium text-gray-900 dark:text-gray-100">
									{persona.name}
								</div>
								{#if !persona.is_active}
									<span class="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500 dark:bg-gray-900 dark:text-gray-400">
										{$i18n.t('Disabled')}
									</span>
								{/if}
							</div>

							<div class="mt-1 text-sm text-gray-500">
								{persona.archetype}
								{#if persona.bound_model_id}
									<span>· {$models.find((model) => model.id === persona.bound_model_id)?.name ?? persona.bound_model_id}</span>
								{/if}
							</div>

							{#if persona.description}
								<div class="mt-2 line-clamp-2 text-sm text-gray-600 dark:text-gray-300">
									{persona.description}
								</div>
							{/if}
						</div>
					</div>

					<div class="flex shrink-0 items-center gap-2">
						<button
							class="rounded-xl border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
							on:click={() => createChatWithPersona(persona.id ?? '')}
						>
							{$i18n.t('Start Chat')}
						</button>
						<button
							class="rounded-xl border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
							on:click={() => goto(`/workspace/personas/edit?id=${encodeURIComponent(persona.id ?? '')}`)}
						>
							{$i18n.t('Edit')}
						</button>
						<button
							class="rounded-xl border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
							on:click={() => createFromExisting(persona)}
						>
							<span class="inline-flex items-center gap-2"><DocumentDuplicate className="size-4" />{$i18n.t('Duplicate')}</span>
						</button>
						<button
							class="rounded-xl border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
							on:click={() => toggleStatus(persona)}
						>
							{persona.is_active ? $i18n.t('Disable') : $i18n.t('Enable')}
						</button>
					</div>
				</div>
			</div>
		{/each}
	</div>
</div>
