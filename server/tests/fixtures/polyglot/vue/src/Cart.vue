<template>
  <button @click="checkout">Buy</button>
</template>

<script setup lang="ts">
import { addItem } from './cart';

function checkout() {
  return addItem('apple');
}
</script>
