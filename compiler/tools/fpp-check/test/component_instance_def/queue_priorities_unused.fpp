port P

active component C {

  async input port p: P priority 2

}

instance c: C base id 0x100 \
  queue size 10 \
  queue priorities {
    priority 2 size 5
    priority 7 size 10
  } \
  stack size 10 * 1024 \
  priority 3
