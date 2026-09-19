<script>
  import { writable } from 'svelte/store';

  export let items = [];

  function add(item) {
    items = [...items, item];
  }
</script>

<button on:click={() => add('apple')}>Add</button>
