module Shop
  class Cart
    def add(item)
      @items << item if item
    end

    def self.build
      new
    end
  end
end
