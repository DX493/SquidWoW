## Project to port T-WoW Maps / content in 3.3.5



The project is still in its early stages. There are still many issues to be resolved. One step at a time.



## The goal ?



Make a Open-Source T-WoW port for 3.3.5, but optimized. By “optimized,” I mean that T-WoW uses patches and game objects that are already present in TBC and WOTLK, so including these game objects serves no practical purpose in patch 3.3.5 (unless the paths differ, in which case it would be better to keep them to avoid confusing the mapping tool and prevent invisible buildings). . If possible, this will slightly optimize the space the game takes up.



### How do I go about this ?



First, to avoid crashing my mapping tool and yours, I’ll need to clean up the map because there are a lot of conflicting UIDs. Since the import treats this as a “clean” project, it could cause the software to crash as soon as it launches. So… we’ll proceed step by step, one ADT at a time, even if it takes a while.



##### “Okay, fine, but do you have any idea how to move forward ? A roadmap ?”



As I said, first I'm going to try to restore everything, ADT / tile by tile, check if m2s and wmos are here. Next ? Well, that alone would already be pretty good. But why not adapt the current T-WoW DBCs, or even the lighting file, for 3.3.5 ? And... after that... consider something that could be ported back down to 1.12? But that's a long way off.



***Known issues :***

* Vanilla "m2 wrong version" in noggit, i can't convert them to 3.3.5 .. But this is not specific for all. I think the only ones i can't convert are M2s that will contain complex animations and particles. I will try to fix this issue but help is welcome. That's the biggest problem i've encountered so far.
* WaterChunk, MCLQ replace by MH2O chunk. Cause crashes if u touch water in Noggit.





###### ***Work i did :***

* **Eastern Kingdom** added. Bug a strange crash occur around Elwynn / Westfall
* **Kalimdor** added. No UID Error .. or less. Kalimdor is pretty clean compare to Eastern Kingdom.



###### ***Next step right now :***

* Fix crash around elwynn. Broken textures in Gilneas city.
* I NEED TO FIND A SOLUTION FOR CUSTOM M2 GODSAKE PLZ T\*RTA OR SOMEONE GIVE ME THE FILES
* WaterChunk is a problem. Need to fix it. I have multiple choice. But one of my solution is the worth case. If i want find a solution i need to rebuilt the map from scratch.
* Taking care of myself, staying healthy and sane
* ...
* *:)*
