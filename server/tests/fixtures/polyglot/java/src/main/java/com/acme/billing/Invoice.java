package com.acme.billing;

public class Invoice {
    private int amount;

    public int total() {
        if (amount > 100) {
            return amount * 2;
        }
        return amount;
    }

    private void reset() {
        amount = 0;
    }
}

interface Priced {
    int price();
}

enum Status { OPEN, PAID }
