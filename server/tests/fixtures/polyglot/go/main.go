package main

import (
	"fmt"

	"example.com/shop/cart"
)

func main() {
	c := &cart.Cart{}
	cart.Add(c, "apple")
	fmt.Println(c.Count())
}
