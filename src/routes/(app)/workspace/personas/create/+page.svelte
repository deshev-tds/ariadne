<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';

	import { personas } from '$lib/stores';
	import { createPersona, getPersonas } from '$lib/apis/personas';
	import PersonaEditor from '$lib/components/workspace/Personas/PersonaEditor.svelte';

	const onSubmit = async (personaInfo) => {
		const res = await createPersona(localStorage.token, personaInfo).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			personas.set(await getPersonas(localStorage.token));
			toast.success('Persona created successfully');
			await goto('/workspace/personas');
		}
	};
</script>

<PersonaEditor {onSubmit} />
