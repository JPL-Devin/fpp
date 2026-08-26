module M {

  port P

  active component Active {

    async input port p: P priority 2

    async input port q: P

  }

  passive component Passive {

    output port p: P

  }

}
