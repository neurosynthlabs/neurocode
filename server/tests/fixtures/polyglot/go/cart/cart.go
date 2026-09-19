package cart

// Cart holds the items a shopper picked.
type Cart struct {
	Items []string
}

type Priced interface {
	Price() int
}

const MaxItems = 50

func Add(c *Cart, item string) {
	if len(c.Items) < MaxItems {
		c.Items = append(c.Items, item)
	}
}

func (c *Cart) Count() int {
	return len(c.Items)
}

func helper() {}
