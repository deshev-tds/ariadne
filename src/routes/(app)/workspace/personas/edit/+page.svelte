<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';

	import { personas } from '$lib/stores';
	import { getPersonaById, getPersonas, updatePersona, type Persona } from '$lib/apis/personas';
	import PersonaEditor from '$lib/components/workspace/Personas/PersonaEditor.svelte';

	let persona: Persona | null = null;

	onMount(async () => {
		const id = $page.url.searchParams.get('id');
		if (!id) {
			goto('/workspace/personas');
			return;
		}

		persona = await getPersonaById(localStorage.token, id).catch(() => null);
		if (!persona) {
			goto('/workspace/personas');
		}
	});

	const onSubmit = async (personaInfo) => {
		const res = await updatePersona(localStorage.token, personaInfo).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			personas.set(await getPersonas(localStorage.token));
			toast.success('Persona updated successfully');
			await goto('/workspace/personas');
		}
	};
</script>

{#if persona}
	<PersonaEditor edit={true} {persona} {onSubmit} />
{/if}
