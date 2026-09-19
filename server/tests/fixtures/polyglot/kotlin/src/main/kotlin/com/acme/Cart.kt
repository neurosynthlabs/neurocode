package com.acme

data class Cart(val items: List<String>) {
    fun count(): Int = items.size
}

interface Priced {
    fun price(): Int
}

object Registry {
    fun register(cart: Cart) {}
}

fun total(cart: Cart): Int {
    return if (cart.count() > 3) 1 else 0
}

private fun hidden() {}
