package com.acme;

import com.acme.billing.Invoice;
import java.util.List;

public class App {
    public static void main(String[] args) {
        System.out.println(new Invoice().total());
    }
}
